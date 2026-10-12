import base64
import importlib
import importlib.util
import io
import sqlite3
import tempfile
import unittest
import wave
import zlib
import struct
import shutil
import subprocess
from contextlib import closing
from pathlib import Path

from flask import Flask

from canvas_routes import canvas_bp
from modules.canvas_project_service import CanvasProjectService


def png_chunk(kind, content):
    return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', zlib.crc32(kind + content) & 0xffffffff)


PNG = b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + png_chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + png_chunk(b'IEND', b'')


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('modules.studio_service'), 'studio service is not implemented')
        self.assertIsNotNone(importlib.util.find_spec('studio_routes'), 'studio routes are not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = str(Path(self.temp.name) / 'canvas.db')
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, MAX_CONTENT_LENGTH=120 * 1024 * 1024)
        self.storage = CanvasProjectService(self.db_path)
        self.studio = importlib.import_module('modules.studio_service').StudioService(self.db_path)
        self.app.extensions.update(canvas_storage=self.storage, studio_service=self.studio)
        self.app.register_blueprint(canvas_bp)
        self.app.register_blueprint(importlib.import_module('studio_routes').studio_bp)
        self.client = self.app.test_client()

    def create(self, kind='product'):
        response = self.client.post('/api/studio/projects', json={'title': '测试作品', 'kind': kind, 'brief': '有具体卖点的产品或连贯故事'})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['project']

    def plan(self, production, **fields):
        return self.client.post('/api/studio/storyboard', json={'source': 'template', 'production': production, **fields})

    def upload(self, category='product', contents=PNG, filename='参考.png', content_type='image/png'):
        return self.client.post('/api/studio/assets', data={'category': category, 'name': '产品参考', 'file': (io.BytesIO(contents), filename, content_type)})

    def assert_valid_graph(self, graph):
        self.studio.validate_graph(graph['nodes'], graph['edges'])
        self.assertEqual(len({n['id'] for n in graph['nodes']}), len(graph['nodes']))
        self.assertEqual(len({e['id'] for e in graph['edges']}), len(graph['edges']))
        touched = {e['source'] for e in graph['edges']} | {e['target'] for e in graph['edges']}
        self.assertEqual(touched, {n['id'] for n in graph['nodes']})

    def test_project_crud_metadata_and_graph_survive_partial_updates(self):
        project = self.create()
        project_id = project['id']
        self.assertEqual(project['production']['kind'], 'product')
        graph = self.plan(project['production']).json
        project.update(nodes=graph['nodes'], edges=graph['edges'], production=graph['production'])
        saved = self.client.put('/api/studio/projects/' + project_id, json=project)
        self.assertEqual(saved.status_code, 200, saved.json)
        changed = self.client.put('/api/studio/projects/' + project_id, json={'title': '改标题', 'production': {'script': '新的故事'}})
        self.assertEqual(changed.json['project']['nodes'], graph['nodes'])
        self.assertEqual(changed.json['project']['production']['shots'], graph['production']['shots'])
        self.assertEqual(changed.json['project']['production']['script'], '新的故事')
        restarted = CanvasProjectService(self.db_path).get_project(user_id='local-canvas-user', project_id=project_id)
        self.assertEqual(restarted['production'], changed.json['project']['production'])
        self.assertEqual(len(self.client.get('/api/studio/projects').json['projects']), 1)
        self.assertEqual(self.client.delete('/api/studio/projects/' + project_id).status_code, 200)
        self.assertEqual(self.client.get('/api/studio/projects/' + project_id).status_code, 404)
        self.assertEqual(self.client.put('/api/studio/projects/missing', json={'title': 'x'}).status_code, 404)

    def test_canvas_autosave_absent_or_null_production_preserves_metadata(self):
        project = self.create('comic')
        for extra in ({}, {'production': None}):
            response = self.client.put('/api/canvas/projects/' + project['id'], json={'title': '画布修改', 'nodes': [], 'edges': [], **extra})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json['project']['production'], project['production'])
        response = self.client.put('/api/canvas/projects/' + project['id'], json={'production': {'script': 'autosave script'}})
        self.assertEqual(response.json['project']['production']['script'], 'autosave script')
        self.assertEqual(response.json['project']['production']['kind'], 'comic')

    def test_existing_sqlite_table_is_migrated_without_losing_canvas(self):
        project = self.storage.upsert_project(user_id='local-canvas-user', title='旧画布', nodes=[], edges=[], viewport={})
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute('ALTER TABLE canvas_projects DROP COLUMN production_json')
        restarted = CanvasProjectService(self.db_path)
        loaded = restarted.get_project(user_id='local-canvas-user', project_id=project['id'])
        self.assertEqual(loaded['title'], '旧画布')
        self.assertIsNone(loaded['production'])

    def test_templates_are_labeled_and_both_create_connected_editable_storyboards(self):
        templates = self.client.get('/api/studio/templates').json['templates']
        self.assertEqual({t['kind'] for t in templates}, {'product', 'comic'})
        for kind in ('product', 'comic'):
            response = self.plan(self.create(kind)['production'])
            self.assertEqual(response.status_code, 200, response.json)
            graph = response.json
            self.assertEqual(graph['source'], 'template')
            self.assertEqual(graph['production']['planning_source'], 'template')
            self.assertGreaterEqual(len(graph['production']['shots']), 3)
            self.assertEqual(len(graph['production']['timeline']), len(graph['production']['shots']))
            self.assert_valid_graph(graph)
            for shot in graph['production']['shots']:
                self.assertTrue(shot['node_ids']['image'])
                self.assertTrue(shot['node_ids']['video'])
                self.assertTrue(shot['node_ids']['video_view'])
            again = self.plan(graph['production']).json
            self.assertEqual([n['id'] for n in again['nodes']], [n['id'] for n in graph['nodes']])

    def test_storyboard_preview_does_not_overwrite_project(self):
        project = self.create()
        response = self.plan(project['production'], project_id=project['id'])
        self.assertEqual(response.status_code, 200)
        loaded = self.client.get('/api/studio/projects/' + project['id']).json['project']
        self.assertEqual(loaded['production']['shots'], [])
        self.assertEqual(loaded['nodes'], [])

    def test_custom_shot_id_episode_order_and_voice_are_preserved(self):
        production = self.create('comic')['production']
        production.update(characters=[{'id': 'hero', 'name': '主角', 'description': '红衣', 'reference_asset_ids': [], 'voice_id': 'voice-custom'}], episodes=[{'id': 'ep1', 'title': '第一集', 'synopsis': '开始', 'script': '故事'}], scenes=[{'id': 'room', 'name': '房间', 'description': '夜景', 'reference_asset_ids': []}], shots=[{'id': 'custom-shot', 'episode_id': 'ep1', 'title': '保留镜头', 'description': '推门', 'image_prompt': '红衣人物', 'video_prompt': '缓慢推镜', 'dialogue': '你好', 'character_ids': ['hero'], 'scene_id': 'room', 'duration_seconds': 4, 'order': 9}])
        response = self.plan(production)
        self.assertEqual(response.status_code, 200, response.json)
        graph = response.json
        self.assertEqual([s['id'] for s in graph['production']['shots']], ['custom-shot'])
        shot = graph['production']['shots'][0]
        self.assertEqual(shot['episode_id'], 'ep1')
        self.assertEqual(shot['order'], 9)
        audio = next(n for n in graph['nodes'] if n['id'] == shot['node_ids']['audio'])
        self.assertEqual(audio['data']['params']['voiceId'], 'voice-custom')
        self.assert_valid_graph(graph)

    def test_real_character_scene_and_product_reference_assets_connect_to_images(self):
        asset = self.upload().json['asset']
        production = self.create()['production']
        production.update(characters=[{'id': 'p', 'name': '商品', 'description': '保持外观', 'reference_asset_ids': [asset['id']], 'voice_id': ''}], scenes=[{'id': 's', 'name': '场景', 'description': '参考布景', 'reference_asset_ids': [asset['id']]}])
        graph = self.plan(production).json
        self.assertTrue(graph['success'], graph)
        refs = [n for n in graph['nodes'] if n['type'] == 'asset']
        self.assertTrue(refs)
        self.assertTrue(all(n['data']['value']['url'] == asset['url'] for n in refs))
        for shot in graph['production']['shots']:
            self.assertTrue(any(e['target'] == shot['node_ids']['image'] and e['targetHandle'] == 'in:ref' for e in graph['edges']))
        self.assert_valid_graph(graph)

    def test_deleted_missing_or_nonimage_reference_returns_clear_error(self):
        production = self.create()['production']
        production['characters'] = [{'id': 'p', 'name': '商品', 'description': '', 'reference_asset_ids': ['missing'], 'voice_id': ''}]
        response = self.plan(production)
        self.assertEqual(response.status_code, 400)
        self.assertIn('missing', response.json['error'])
        self.assertIn('素材', response.json['error'])

    def test_asset_library_upload_search_download_delete_and_generated_names(self):
        uploaded = self.upload(filename='../../unsafe.png')
        self.assertEqual(uploaded.status_code, 200, uploaded.json)
        asset = uploaded.json['asset']
        self.assertEqual(asset['category'], 'product')
        self.assertEqual(asset['media_type'], 'image')
        self.assertNotIn('unsafe', asset['url'])
        with self.client.get(asset['url']) as media:
            self.assertEqual(media.status_code, 200)
            self.assertEqual(media.data, PNG)
            self.assertEqual(media.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(len(self.client.get('/api/studio/assets?category=product&q=参考').json['assets']), 1)
        self.assertEqual(self.client.get('/api/studio/assets?category=audio').json['assets'], [])
        with self.client.get(asset['url'] + '?download=1') as media:
            self.assertIn('attachment', media.headers['Content-Disposition'])
        self.assertEqual(self.client.delete('/api/studio/assets/' + asset['id']).status_code, 200)
        self.assertEqual(self.client.get(asset['url']).status_code, 404)
        self.assertEqual(self.client.delete('/api/studio/assets/' + asset['id']).status_code, 404)

    def test_valid_audio_upload_and_invalid_upload_types_sizes_and_category(self):
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0\0' * 800)
        response = self.upload('audio', audio.getvalue(), 'voice.wav', 'audio/wav')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['asset']['media_type'], 'audio')
        for category, contents, name, mime in [('other', PNG, 'x.png', 'image/png'), ('product', b'<svg/>', 'x.svg', 'image/svg+xml'), ('character', b'\x89PNG\r\n\x1a\nabc', 'x.png', 'image/png'), ('product', PNG, 'x.png', 'text/html'), ('video', PNG, 'x.png', 'image/png'), ('product', b'', 'x.png', 'image/png')]:
            self.assertEqual(self.upload(category, contents, name, mime).status_code, 400)
        self.studio.max_asset_bytes = 20
        self.assertEqual(self.upload().status_code, 413)

    def test_ai_requires_configured_service_and_uses_provider_production(self):
        production = self.create()['production']
        response = self.client.post('/api/studio/storyboard', json={'source': 'ai', 'production': production})
        self.assertEqual(response.status_code, 503)
        planned = self.plan(production).json['production']
        planned['shots'][0]['title'] = '供应商分镜'
        class Planner:
            def plan_storyboard(inner, value):
                self.assertEqual(value['brief'], production['brief'])
                return planned
        self.app.extensions['generation_service'] = Planner()
        response = self.client.post('/api/studio/storyboard', json={'source': 'ai', 'production': production})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['production']['shots'][0]['title'], '供应商分镜')
        self.assertEqual(response.json['source'], 'ai')
        self.assert_valid_graph(response.json)

    def test_invalid_production_bounded_types_refs_and_json_are_rejected(self):
        project = self.create()
        for payload in ([], {'kind': 'unknown'}, {'kind': 'product', 'characters': {}}, {'kind': 'comic', 'shots': [{}] * 201}, {'kind': 'product', 'duration_seconds': True}, {'kind': 'product', 'aspect_ratio': 'javascript:'}):
            self.assertEqual(self.client.post('/api/studio/projects', json=payload).status_code, 400, payload)
        for field in ('characters', 'scenes', 'episodes', 'shots', 'timeline'):
            response = self.client.put('/api/studio/projects/' + project['id'], json={'production': {field: [7]}})
            self.assertEqual(response.status_code, 400, (field, response.json))
        production = self.plan(project['production']).json['production']
        production['shots'][0]['character_ids'] = ['nonexistent']
        self.assertEqual(self.plan(production).status_code, 400)
        self.assertEqual(self.plan(project['production'], source='invalid').status_code, 400)

    def test_invalid_graph_ports_cycles_and_missing_nodes_are_rejected(self):
        project = self.create()
        graph = self.plan(project['production']).json
        edge = dict(graph['edges'][0])
        for patch in ({'targetHandle': 'wrong'}, {'source': 'missing'}, {'sourceHandle': 'wrong'}):
            invalid = {**edge, **patch}
            response = self.client.put('/api/studio/projects/' + project['id'], json={'nodes': graph['nodes'], 'edges': [invalid]})
            self.assertEqual(response.status_code, 400, response.json)
        prompts = [{'id': i, 'type': 'prompt', 'position': {'x': 0, 'y': 0}, 'data': {}} for i in ('a', 'b')]
        edges = [{'id': str(i), 'source': s, 'target': t, 'sourceHandle': 'out', 'targetHandle': 'in:context', 'data': {'mediaType': 'text', 'order': 0}} for i, (s, t) in enumerate([('a', 'b'), ('b', 'a')])]
        self.assertEqual(self.client.put('/api/studio/projects/' + project['id'], json={'nodes': prompts, 'edges': edges}).status_code, 400)

    def test_cross_site_writes_and_remote_hosts_are_rejected(self):
        self.assertEqual(self.client.post('/api/studio/projects', json={'kind': 'product'}, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/studio/assets', headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        self.assertEqual(self.client.get('/api/studio/templates', headers={'Host': 'evil.example'}).status_code, 403)

    def test_corrupt_image_crc_and_encoded_payload_are_rejected(self):
        corrupt = bytearray(PNG)
        corrupt[45] ^= 1
        self.assertEqual(self.upload(contents=bytes(corrupt)).status_code, 400)
        truncated_jpeg = b'\xff\xd8\xff' + b'not-a-real-image' * 3 + b'\xff\xd9'
        self.assertEqual(self.upload(contents=truncated_jpeg, filename='x.jpg', content_type='image/jpeg').status_code, 400)

    def test_episode_filtered_storyboard_leaves_other_episode_shots_intact(self):
        production = self.create('comic')['production']
        production['episodes'] = [{'id': 'a', 'title': '第一集', 'synopsis': '开场', 'script': ''}, {'id': 'b', 'title': '第二集', 'synopsis': '后续', 'script': ''}]
        full = self.plan(production).json
        self.assertEqual(len(full['production']['shots']), 6)
        filtered = self.plan(full['production'], episode_id='b')
        self.assertEqual(filtered.status_code, 200, filtered.json)
        self.assertEqual(len(filtered.json['production']['shots']), 6)
        self.assertEqual({n['data']['episode_id'] for n in filtered.json['nodes'] if 'episode_id' in n['data']}, {'b'})
        self.assert_valid_graph(filtered.json)

    def test_reference_capacity_and_duplicate_node_ids_have_clear_errors(self):
        asset_ids = [self.upload().json['asset']['id'] for _ in range(7)]
        production = self.create()['production']
        production['characters'] = [{'id': 'p', 'name': '商品', 'description': '', 'reference_asset_ids': asset_ids[:6], 'voice_id': ''}]
        production['scenes'] = [{'id': 's', 'name': '场景', 'description': '', 'reference_asset_ids': asset_ids[6:]}]
        response = self.plan(production)
        self.assertEqual(response.status_code, 400)
        self.assertIn('超过 6', response.json['error'])
        graph = self.plan(self.create()['production']).json
        graph['production']['shots'][1]['node_ids'] = graph['production']['shots'][0]['node_ids']
        self.assertEqual(self.plan(graph['production']).status_code, 400)

    def test_invalid_enums_unhashable_ids_and_large_graph_return_json_400(self):
        project = self.create()
        invalids = [{'kind': []}, {'kind': {}}, {'characters': [{'id': []}]}, {'shots': [{'id': 's', 'character_ids': [{}]}]}]
        for invalid in invalids:
            response = self.client.put('/api/studio/projects/' + project['id'], json={'production': invalid})
            self.assertEqual(response.status_code, 400, response.json)
            self.assertFalse(response.json['success'])
        self.assertEqual(self.client.post('/api/studio/storyboard', json={'source': [], 'production': {}}).status_code, 400)
        self.assertEqual(self.client.put('/api/studio/projects/' + project['id'], json={'nodes': [{}] * 1001}).status_code, 400)

    def test_same_source_ids_across_projects_do_not_overwrite_other_owner(self):
        project = self.storage.upsert_project(user_id='other-user', project_id='private-project', title='别人的画布', nodes=[], edges=[], viewport={})
        with self.assertRaises(importlib.import_module('modules.canvas_project_service').CanvasProjectStorageError):
            self.storage.upsert_project(user_id='local-canvas-user', project_id=project['id'], title='不能覆盖', nodes=[], edges=[], viewport={})
        self.assertEqual(self.storage.get_project(user_id='other-user', project_id=project['id'])['title'], '别人的画布')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'local FFmpeg/FFprobe required')
    def test_valid_local_video_upload_and_corrupted_container_rejection(self):
        video = Path(self.temp.name) / 'clip.mp4'
        result = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x64:d=0.3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], capture_output=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        uploaded = self.upload('video', video.read_bytes(), 'clip.mp4', 'video/mp4')
        self.assertEqual(uploaded.status_code, 200, uploaded.json)
        self.assertEqual(uploaded.json['asset']['media_type'], 'video')
        fake = struct.pack('>I', 32) + b'ftypisom' + b'\0' * 64
        self.assertEqual(self.upload('video', fake, 'fake.mp4', 'video/mp4').status_code, 400)

    def test_canvas_invalid_payload_types_and_limit_return_json_error(self):
        for payload in ([], {'nodes': {}}, {'edges': False}, {'production': []}, {'nodes': [7]}):
            response = self.client.post('/api/canvas/projects', json=payload)
            self.assertEqual(response.status_code, 400, response.json)
        response = self.client.get('/api/canvas/projects?limit=wrong')
        self.assertEqual(response.status_code, 400, response.json)


if __name__ == '__main__':
    unittest.main()
