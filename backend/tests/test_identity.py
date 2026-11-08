import base64
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1sAAAAASUVORK5CYII=')


class IdentityCardTests(unittest.TestCase):
    def setUp(self):
        from modules.studio_service import StudioService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.studio = StudioService(Path(self.temp.name) / 'canvas.db')
        self.media = self.studio.media_dir

    def store_image(self, asset_id='asset-face'):
        (self.media / (asset_id + '.png')).write_bytes(PNG)
        with self.studio._connection() as conn:
            conn.execute('INSERT INTO studio_assets (id,name,category,media_type,filename,mime_type,created_at) VALUES (?,?,?,?,?,?,?)',
                         (asset_id, '角色参考', 'character', 'image', asset_id + '.png', 'image/png', 'now'))
        return asset_id

    def production(self, locked=True, with_ref=True):
        refs = ['asset-face'] if with_ref else []
        return {'kind': 'comic', 'brief': '少年冒险故事', 'characters': [
            {'id': 'c1', 'name': '主角', 'description': '勇敢的少年', 'reference_asset_ids': refs,
             'appearance': '黑色短发、圆脸', 'outfit': '白色校服', 'identity_locked': locked}],
            'scenes': [], 'episodes': [], 'shots': []}

    def test_normalize_keeps_identity_fields(self):
        from modules.studio_service import normalize_production
        result = normalize_production({'characters': [{'id': 'c1', 'name': '主角', 'appearance': '黑色短发',
                                                       'outfit': '白色校服', 'identity_locked': True}]})
        character = result['characters'][0]
        self.assertEqual(character['appearance'], '黑色短发')
        self.assertEqual(character['outfit'], '白色校服')
        self.assertIs(character['identity_locked'], True)

    def test_identity_locked_must_be_boolean(self):
        from modules.studio_service import StudioValidationError, normalize_production
        with self.assertRaises(StudioValidationError):
            normalize_production({'characters': [{'id': 'c1', 'identity_locked': 'yes'}]})

    def test_locked_character_without_reference_image_rejected_at_build(self):
        from modules.studio_service import StudioValidationError
        with self.assertRaises(StudioValidationError) as caught:
            self.studio.build_storyboard(self.production(locked=True, with_ref=False), source='template')
        self.assertIn('参考图片', str(caught.exception))

    def test_unlocked_character_without_reference_image_builds_normally(self):
        graph = self.studio.build_storyboard(self.production(locked=False, with_ref=False), source='template')
        self.assertTrue(graph['nodes'])

    def test_locked_identity_is_injected_into_every_shot_prompt(self):
        self.store_image()
        graph = self.studio.build_storyboard(self.production(), source='template')
        prompts = [node for node in graph['nodes'] if node['type'] == 'prompt' and '画面提示词' in node['data']['title']]
        self.assertTrue(prompts)
        for prompt in prompts:
            self.assertIn('形象锁定', prompt['data']['value']['text'])
            self.assertIn('黑色短发', prompt['data']['value']['text'])
            self.assertIn('白色校服', prompt['data']['value']['text'])

    def test_locked_character_reference_images_are_wired_to_every_image_node(self):
        self.store_image()
        graph = self.studio.build_storyboard(self.production(), source='template')
        image_nodes = [node for node in graph['nodes'] if node['type'] == 'image-gen']
        self.assertTrue(image_nodes)
        for image in image_nodes:
            refs = [edge for edge in graph['edges'] if edge['target'] == image['id'] and edge['targetHandle'] == 'in:ref']
            self.assertEqual(len(refs), 1, '锁定角色的参考图必须连接到每个镜头的图片节点')


class ImageEditNodeTests(unittest.TestCase):
    def setUp(self):
        from modules.generation_service import GenerationService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'canvas.db'
        self.settings = {'IMAGE_BASE_URL': 'https://provider.example/v1', 'IMAGE_API_KEY': 'test-secret', 'IMAGE_MODEL': 'gpt-image-2'}
        self.service = GenerationService(self.db, settings={**self.settings})
        self.addCleanup(self.service.close)
        self.service.media_dir.mkdir(exist_ok=True)
        (self.service.media_dir / 'shot.png').write_bytes(PNG)
        (self.service.media_dir / 'face.png').write_bytes(PNG)

    def payload(self, run='edit-run', with_image=True, with_ref=True):
        inputs = {'prompt': ['保持角色一致，把背景换成夜晚街道']}
        if with_image:
            inputs['image'] = ['/studio-media/shot.png']
        if with_ref:
            inputs['ref'] = ['/studio-media/face.png']
        return {'node_type': 'image-edit', 'node_id': 'edit-one', 'project_id': 'project-one',
                'client_run_id': run, 'params': {'model': 'gpt-image-2', 'ratio': '1:1'}, 'inputs': inputs}

    def wait_job(self, job_id):
        for _ in range(150):
            job = self.service.get_job(job_id)
            if job['status'] in ('succeeded', 'failed', 'canceled'):
                return job
            time.sleep(.02)
        self.fail('background job did not finish')

    def test_image_edit_posts_input_image_then_reference(self):
        with patch.object(self.service, '_request_json', return_value={'data': [{'b64_json': base64.b64encode(PNG).decode()}]}) as supplier:
            receipt = self.service.submit_node(self.payload())
            job = self.wait_job(receipt['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded', job)
            self.assertEqual(supplier.call_args.args[1], '/images/edits')
            files = supplier.call_args.kwargs['files']
            self.assertEqual(files[0][1][1], PNG, '第一个文件必须是待编辑图片')
            self.assertEqual(len(files), 2, '参考图跟在待编辑图片之后')
            self.assertEqual(supplier.call_args.kwargs['payload']['n'], 1)
            self.assertNotIn('size', supplier.call_args.kwargs['payload'], '修图保持原图画幅，不应发送尺寸')

    def test_image_edit_requires_image_input(self):
        from modules.generation_service import GenerationError
        with self.assertRaises(GenerationError) as caught:
            self.service.submit_node(self.payload(run='edit-no-image', with_image=False))
        self.assertEqual(caught.exception.status, 400)

    def test_image_edit_requires_instruction(self):
        from modules.generation_service import GenerationError
        payload = self.payload(run='edit-no-prompt')
        payload['inputs']['prompt'] = []
        with self.assertRaises(GenerationError):
            self.service.submit_node(payload)

    def test_image_edit_participates_in_content_cache(self):
        with patch.object(self.service, '_request_json', return_value={'data': [{'b64_json': base64.b64encode(PNG).decode()}]}) as supplier:
            first = self.service.submit_node(self.payload(run='edit-cache-a'))
            self.wait_job(first['jobs'][0]['id'])
            second = self.service.submit_node(self.payload(run='edit-cache-b'))
            job = self.service.get_job(second['jobs'][0]['id'])
            self.assertEqual(supplier.call_count, 1)
            self.assertEqual(job['status'], 'succeeded')
            self.assertTrue(job['provider_id'].startswith('cache:'))


if __name__ == '__main__':
    unittest.main()
