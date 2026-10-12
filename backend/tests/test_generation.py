import base64
import importlib.util
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from flask import Flask

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1sAAAAASUVORK5CYII=')


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('modules.generation_service'), 'generation backend must exist')
        from modules.generation_service import GenerationService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'canvas.db'
        self.service = GenerationService(self.db, settings={})
        self.addCleanup(self.service.close)

    def payload(self, kind='image-gen', run='run-one'):
        return {'node_type': kind, 'node_id': 'node-one', 'project_id': 'project-one',
                'client_run_id': run, 'params': {}, 'inputs': {'prompt': ['a red product']}}

    def wait_job(self, service, job_id):
        for _ in range(150):
            job = service.get_job(job_id)
            if job['status'] in ('succeeded', 'failed', 'canceled'):
                return job
            time.sleep(.02)
        self.fail('background job did not finish')

    def configured(self, **overrides):
        from modules.generation_service import GenerationService
        settings = {'IMAGE_BASE_URL': 'https://provider.example/v1', 'IMAGE_API_KEY': 'test-secret',
                    'IMAGE_MODEL': 'gpt-image-2', **overrides}
        service = GenerationService(self.db, settings)
        self.addCleanup(service.close)
        return service

    def test_unconfigured_is_503_and_does_not_create_fake_job(self):
        from modules.generation_service import GenerationError
        with self.assertRaises(GenerationError) as caught:
            self.service.submit_node(self.payload())
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.service.list_jobs(), [])
        self.assertNotIn('test-secret', json.dumps(self.service.settings_status()))

    def test_image_protocol_persists_artifact_and_idempotency(self):
        service = self.configured()
        with patch.object(service, '_request_json', return_value={'data': [{'b64_json': base64.b64encode(PNG).decode()}]}) as supplier:
            first = service.submit_node(self.payload())
            second = service.submit_node(self.payload())
            self.assertEqual(first['jobs'], second['jobs'])
            job = self.wait_job(service, first['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded', job)
            self.assertEqual(supplier.call_count, 1)
            self.assertEqual(supplier.call_args.args[1], '/images/generations')
            self.assertEqual(supplier.call_args.kwargs['payload']['model'], 'gpt-image-2')
            self.assertEqual((service.media_dir / Path(job['outputs'][0]['url']).name).read_bytes(), PNG)
            from modules.generation_service import GenerationError
            changed = self.payload()
            changed['inputs']['prompt'] = ['different']
            with self.assertRaises(GenerationError) as error:
                service.submit_node(changed)
            self.assertEqual(error.exception.status, 409)
        service.close()
        restored = self.configured()
        self.assertEqual(restored.submit_node(self.payload())['jobs'], first['jobs'])

    def test_references_use_image_edit_protocol_and_local_bytes(self):
        service = self.configured()
        (service.media_dir / 'reference.png').write_bytes(PNG)
        payload = self.payload()
        payload['inputs']['ref'] = ['/studio-media/reference.png']
        with patch.object(service, '_request_json', return_value={'data': [{'b64_json': base64.b64encode(PNG).decode()}]}) as supplier:
            result = service.submit_node(payload)
            self.assertEqual(self.wait_job(service, result['jobs'][0]['id'])['status'], 'succeeded')
            self.assertEqual(supplier.call_args.args[1], '/images/edits')
            self.assertEqual(supplier.call_args.kwargs['files'][0][1][1], PNG)

    def test_recovery_marks_interrupted_job_failed_without_resubmitting(self):
        self.service.close()
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO generation_jobs(id,kind,status,node_id,project_id,client_run_id,request_json,request_hash,outputs_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               ('lost', 'image', 'running', 'n', 'p', 'interrupted', '{}', 'x', '[]', 'now', 'now'))
        from modules.generation_service import GenerationService
        restored = GenerationService(self.db, {})
        self.addCleanup(restored.close)
        job = restored.get_job('lost')
        self.assertEqual(job['status'], 'failed')
        self.assertIn('重试', job['error'])

    def test_run_receipt_can_be_recovered_after_client_loses_submit_response(self):
        service = self.configured()
        with patch.object(service, '_request_json', return_value={'data': [{'b64_json': base64.b64encode(PNG).decode()}]}):
            receipt = service.submit_node(self.payload(run='client-recovery'))
            recovered = service.get_run('client-recovery')
            self.assertEqual(recovered['jobs'], receipt['jobs'])
            self.wait_job(service, recovered['jobs'][0]['id'])
        from modules.generation_service import GenerationError
        with self.assertRaises(GenerationError) as error:
            service.get_run('does-not-exist')
        self.assertEqual(error.exception.status, 404)

    def test_openai_video_reference_matches_requested_frame_dimensions(self):
        import io
        from PIL import Image
        service = self.configured(VIDEO_BASE_URL='https://provider.example/v1', VIDEO_API_KEY='test-video', VIDEO_MODEL='compat-video', VIDEO_PROTOCOL='openai')
        reference = service.media_dir / 'reference.png'
        Image.new('RGB', (1024, 1536), 'green').save(reference)
        payload = self.payload('video-gen', 'reference-resize')
        payload['params'] = {'ratio': '9:16', 'durationSeconds': 4}
        payload['inputs']['first'] = ['/studio-media/reference.png']
        with patch.object(service, '_request_json', return_value={'id': 'provider-video', 'status': 'failed'}) as supplier:
            self.wait_job(service, service.submit_node(payload)['jobs'][0]['id'])
            body = supplier.call_args.kwargs['payload']
            data = base64.b64decode(body['input_reference']['image_url'].split(',', 1)[1])
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.size, (720, 1280))

    def test_cancel_running_job_prevents_late_success_and_retry_is_explicit(self):
        service = self.configured()
        entered, release = threading.Event(), threading.Event()
        def supplier(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {'data': [{'b64_json': base64.b64encode(PNG).decode()}]}
        with patch.object(service, '_request_json', side_effect=supplier):
            result = service.submit_node(self.payload())
            job_id = result['jobs'][0]['id']
            self.assertTrue(entered.wait(2))
            self.assertEqual(service.cancel_job(job_id)['status'], 'canceled')
            release.set()
            time.sleep(.1)
            self.assertEqual(service.get_job(job_id)['status'], 'canceled')
        with patch.object(service, '_request_json', return_value={'data': [{'b64_json': base64.b64encode(PNG).decode()}]}):
            retry = service.retry_job(job_id)
            self.assertNotEqual(retry['id'], job_id)
            self.assertEqual(self.wait_job(service, retry['id'])['status'], 'succeeded')

    def test_provider_error_never_exposes_credentials(self):
        service = self.configured()
        with patch.object(service, '_request_json', side_effect=RuntimeError('test-secret https://secret.example?token=test-secret')):
            result = service.submit_node(self.payload())
            job = self.wait_job(service, result['jobs'][0]['id'])
            self.assertEqual(job['status'], 'failed')
            self.assertNotIn('test-secret', json.dumps(job))

    def test_audio_protocol_uses_configured_voice_and_writes_bytes(self):
        service = self.configured(AUDIO_BASE_URL='https://provider.example/v1', AUDIO_API_KEY='audio-test', AUDIO_MODEL='tts-test', AUDIO_VOICES='alloy,nova')
        payload = self.payload('audio-gen')
        payload['inputs'] = {'text': ['商品宣传配音']}
        payload['params'] = {'voiceId': 'nova', 'speed': 1.25}
        with patch.object(service, '_request_binary', return_value=(b'RIFF' + b'\x00'*4 + b'WAVE' + b'\x00'*32, 'audio/wav')) as supplier:
            result = service.submit_node(payload)
            self.assertEqual(self.wait_job(service, result['jobs'][0]['id'])['status'], 'succeeded')
            self.assertEqual(supplier.call_args.args[1], '/audio/speech')
            self.assertEqual(supplier.call_args.kwargs['payload']['voice'], 'nova')
            self.assertEqual(supplier.call_args.kwargs['payload']['input'], '商品宣传配音')

    def test_seedance_video_protocol_polls_and_records_provider_id(self):
        service = self.configured(VIDEO_BASE_URL='https://provider.example/api/v3', VIDEO_API_KEY='video-test', VIDEO_MODEL='seedance-test', VIDEO_PROTOCOL='seedance', VIDEO_POLL_INTERVAL=.01)
        payload = self.payload('video-gen')
        payload['params'] = {'durationSeconds': 5, 'aspectRatio': '9:16', 'resolution': '720p'}
        (service.media_dir / 'frame.png').write_bytes(PNG)
        payload['inputs']['first'] = ['/studio-media/frame.png']
        responses = [{'id': 'task-123'}, {'status': 'succeeded', 'content': {'video_url': 'https://cdn.example/movie.mp4'}}]
        with patch.object(service, '_request_json', side_effect=responses) as supplier, patch.object(service.downloader, 'download', return_value={'kind': 'video', 'url': '/studio-media/movie.mp4'}):
            result = service.submit_node(payload)
            job = self.wait_job(service, result['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded', job)
            self.assertEqual(job['provider_id'], 'task-123')
            first = supplier.call_args_list[0]
            self.assertEqual(first.args[1], '/contents/generations/tasks')
            self.assertEqual(first.kwargs['payload']['duration'], 5)
            self.assertEqual(first.kwargs['payload']['content'][1]['role'], 'first_frame')
            self.assertTrue(first.kwargs['payload']['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))

    def test_openai_video_protocol_downloads_authenticated_content_without_reposting(self):
        service = self.configured(VIDEO_BASE_URL='https://provider.example/v1', VIDEO_API_KEY='video-test', VIDEO_MODEL='sora-test', VIDEO_PROTOCOL='openai', VIDEO_POLL_INTERVAL=.01)
        payload = self.payload('video-gen')
        responses = [{'id': 'video-123', 'status': 'queued'}, {'status': 'completed'}]
        with patch.object(service, '_request_json', side_effect=responses) as supplier, patch.object(service, '_request_binary', return_value=(b'\x00\x00\x00\x18ftypisom'+b'\x00'*20, 'video/mp4')) as content:
            job = self.wait_job(service, service.submit_node(payload)['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded', job)
            self.assertEqual(supplier.call_args_list[0].args[1], '/videos')
            self.assertEqual(content.call_args.args[1], '/videos/video-123/content')

    def test_text_protocol_validates_storyboard_and_graph_instead_of_accepting_broken_json(self):
        service = self.configured(TEXT_BASE_URL='https://provider.example/v1', TEXT_API_KEY='text-test', TEXT_MODEL='text-test')
        response = lambda data: {'choices': [{'message': {'content': json.dumps(data)}}]}
        production = {'kind': 'comic', 'brief': '主角救朋友', 'shots': []}
        planned = {'kind': 'comic', 'brief': '主角救朋友', 'shots': [{'id': 's1', 'title': '开场', 'image_prompt': 'hero', 'video_prompt': 'hero runs', 'duration_seconds': 5, 'order': 0}]}
        planned['shots'] += [{**planned['shots'][0], 'id': f's{index+1}', 'order': index} for index in (1, 2)]
        with patch.object(service, '_request_json', return_value=response(planned)):
            result = service.plan_storyboard(production)
            self.assertEqual(result['shots'][0]['id'], 's1')
        with patch.object(service, '_request_json', return_value=response({'shots': 'invalid'})):
            from modules.generation_service import GenerationError
            with self.assertRaises(GenerationError):
                service.plan_storyboard(production)
        with patch.object(service, '_request_json', return_value=response({**planned, 'shots': planned['shots'][:1]})):
            with self.assertRaises(GenerationError):
                service.plan_storyboard(production)
        graph = {'title': 'workflow', 'nodes': [{'id': 'p', 'type': 'prompt', 'text': 'product', 'x': 0, 'y': 0}, {'id': 'i', 'type': 'image-gen', 'x': 300, 'y': 0}],
                 'edges': [{'source': 'p', 'target': 'i', 'targetHandle': 'in:prompt', 'data': {'order': 0, 'mediaType': 'text'}}]}
        with patch.object(service, '_request_json', return_value=response(graph)):
            self.assertEqual(service.agent_plan('做一张产品图')['title'], 'workflow')
        graph['edges'][0]['target'] = 'missing'
        with patch.object(service, '_request_json', return_value=response(graph)):
            with self.assertRaises(GenerationError):
                service.agent_plan('做一张产品图')

    def test_bounded_executor_rejects_new_work_but_accepts_idempotent_replay(self):
        service = self.configured(MAX_WORKERS=1, MAX_PENDING=1)
        entered, release = threading.Event(), threading.Event()
        def supplier(*args, **kwargs):
            entered.set()
            release.wait(2)
            return {'data': [{'b64_json': base64.b64encode(PNG).decode()}]}
        with patch.object(service, '_request_json', side_effect=supplier):
            first = service.submit_node(self.payload())
            self.assertTrue(entered.wait(2))
            self.assertEqual(service.submit_node(self.payload())['jobs'], first['jobs'])
            from modules.generation_service import GenerationError
            with self.assertRaises(GenerationError) as error:
                service.submit_node(self.payload(run='run-two'))
            self.assertEqual(error.exception.status, 429)
            release.set()
            self.wait_job(service, first['jobs'][0]['id'])

    def test_routes_have_structured_errors_and_kind_guard(self):
        self.assertIsNotNone(importlib.util.find_spec('generation_routes'))
        from generation_routes import generation_bp
        from studio_routes import studio_bp
        from modules.studio_service import StudioService
        app = Flask(__name__)
        app.extensions['generation_service'] = self.service
        app.extensions['canvas_storage'] = None
        app.extensions['studio_service'] = StudioService(self.db)
        app.register_blueprint(generation_bp)
        app.register_blueprint(studio_bp)
        client = app.test_client()
        for path in ('/api/canvas/nodes/run', '/api/canvas/agent/plan', '/api/canvas/prompt-assist'):
            response = client.post(path, json={})
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json['success'])
        response = client.get('/api/canvas/jobs/video/missing')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json['success'])
        self.assertEqual(client.get('/api/studio/settings').status_code, 200)
        (self.service.media_dir / 'test.png').write_bytes(PNG)
        with client.get('/studio-media/test.png?download=1') as response:
            self.assertEqual(response.data, PNG)
            self.assertIn('attachment', response.headers['Content-Disposition'])

    def test_public_downloader_blocks_private_and_redirect_destinations(self):
        from modules.generation_service import GenerationError, PublicMediaDownloader
        downloader = PublicMediaDownloader(self.service.media_dir, max_bytes=1024)
        with patch('modules.generation_service.socket.getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(GenerationError):
                downloader.download('https://provider.example/file.png', 'image')
        with self.assertRaises(GenerationError):
            downloader.download('file:///C:/secret', 'image')
        def addresses(host, *_args, **_kwargs):
            return [(2, 1, 6, '', ('127.0.0.1' if host == '127.0.0.1' else '8.8.8.8', 443))]
        with patch('modules.generation_service.socket.getaddrinfo', side_effect=addresses), patch.object(downloader, '_open', return_value=(302, {'location': 'https://127.0.0.1/secret'}, None)) as opened:
            with self.assertRaises(GenerationError):
                downloader.download('https://provider.example/file.png', 'image')
            self.assertEqual(opened.call_count, 1, 'private redirect must be blocked before opening another connection')

    def test_downloader_checks_content_type_size_magic_and_closes_response(self):
        from modules.generation_service import GenerationError, PublicMediaDownloader
        from io import BytesIO
        downloader = PublicMediaDownloader(self.service.media_dir, max_bytes=1024)
        public = [(2, 1, 6, '', ('8.8.8.8', 443))]
        for headers, data in (({'content-type': 'text/html'}, b'<html>'),
                              ({'content-type': 'image/png', 'content-length': '1025'}, PNG),
                              ({'content-type': 'image/png'}, b'x'*1025),
                              ({'content-type': 'image/png'}, b'not an image')):
            stream = BytesIO(data)
            with patch('modules.generation_service.socket.getaddrinfo', return_value=public), patch.object(downloader, '_open', return_value=(200, headers, stream)):
                with self.assertRaises(GenerationError):
                    downloader.download('https://cdn.example/image.png', 'image')
            self.assertTrue(stream.closed)
        with patch('modules.generation_service.socket.getaddrinfo', return_value=public), patch.object(downloader, '_open', return_value=(200, {'content-type': 'image/png'}, BytesIO(PNG))):
            output = downloader.download('https://cdn.example/image.png', 'image')
            self.assertEqual((self.service.media_dir / Path(output['url']).name).read_bytes(), PNG)

    def test_invalid_model_payload_and_nonimage_reference_are_rejected_before_submission(self):
        from modules.generation_service import GenerationError
        service = self.configured()
        payload = self.payload()
        payload['params']['model'] = []
        with patch.object(service, '_request_json', return_value={'id': 'blocked', 'status': 'failed'}):
            with self.assertRaises(GenerationError):
                service.submit_node(payload)
        (service.media_dir / 'fake.png').write_bytes(b'not-image')
        payload = self.payload()
        payload['inputs']['ref'] = ['/studio-media/fake.png']
        with self.assertRaises(GenerationError):
            service.submit_node(payload)
        self.assertEqual(service.list_jobs(), [])

    def test_catalog_selection_preserves_explicitly_configured_additional_model(self):
        service = self.configured(IMAGE_MODEL='primary-image', IMAGE_MODELS='primary-image,gpt-image-2')
        payload = self.payload()
        payload['params']['model'] = 'gpt-image-2'
        with patch.object(service, '_request_json', return_value={'data': [{'b64_json': base64.b64encode(PNG).decode()}]}) as supplier:
            job = self.wait_job(service, service.submit_node(payload)['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded')
            self.assertEqual(supplier.call_args.kwargs['payload']['model'], 'gpt-image-2')

    def test_routes_block_cross_site_and_report_wrong_kind_as_404(self):
        from generation_routes import generation_bp
        app = Flask(__name__)
        app.extensions['generation_service'] = self.service
        app.register_blueprint(generation_bp)
        client = app.test_client()
        self.assertEqual(client.post('/api/canvas/nodes/run', json={}, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(client.get('/api/studio/settings', headers={'Host': 'evil.example'}).status_code, 403)

    def test_http_transport_sets_auth_and_never_retries_or_follows_redirects(self):
        from unittest.mock import MagicMock
        from modules.generation_service import GenerationError
        service = self.configured()
        session, response = MagicMock(), MagicMock()
        session.__enter__.return_value = session
        session.request.return_value = response
        response.status_code = 200
        response.headers = {'Content-Type': 'application/json'}
        response.iter_content.return_value = [b'{"data":[]}']
        with patch('modules.generation_service.requests.Session', return_value=session):
            self.assertEqual(service._request_json('image', '/images/generations', payload={'model': 'gpt-image-2'}), {'data': []})
        sent = session.request.call_args
        self.assertEqual(sent.args, ('POST', 'https://provider.example/v1/images/generations'))
        self.assertFalse(sent.kwargs['allow_redirects'])
        self.assertEqual(sent.kwargs['headers']['Authorization'], 'Bearer test-secret')
        self.assertFalse(session.trust_env)
        response.status_code = 302
        with patch('modules.generation_service.requests.Session', return_value=session):
            with self.assertRaises(GenerationError) as error:
                service._request_json('image', '/images/generations', payload={})
        self.assertNotIn('test-secret', str(error.exception))
        self.assertEqual(session.request.call_count, 2, 'no automatic POST retry')

    def test_export_service_routes_persist_real_job_shape_without_supplier(self):
        from generation_routes import generation_bp
        app = Flask(__name__)
        app.extensions['generation_service'] = self.service
        app.register_blueprint(generation_bp)
        client = app.test_client()
        (self.service.media_dir / 'a.mp4').write_bytes(b'local')
        with patch.object(self.service.pipeline, 'export', return_value={'kind': 'video', 'url': '/studio-media/result.mp4'}):
            response = client.post('/api/studio/export', json={'project_id': 'p', 'clips': [{'url': '/studio-media/a.mp4'}]})
            self.assertEqual(response.status_code, 200, response.json)
            job_id = response.json['job']['id']
            self.assertEqual(self.wait_job(self.service, job_id)['status'], 'succeeded')
            self.assertEqual(client.get('/api/canvas/jobs/export/' + job_id).json['job']['outputs'][0]['kind'], 'video')
            self.assertEqual(client.get('/api/canvas/jobs/video/' + job_id).status_code, 404)
            self.assertEqual(len(client.get('/api/studio/jobs?project_id=p').json['jobs']), 1)
        compose = self.payload('compose')
        compose['inputs'] = {'video': ['/studio-media/a.mp4']}
        with patch.object(self.service.pipeline, 'export', return_value={'kind': 'video', 'url': '/studio-media/composed.mp4'}):
            result = self.service.submit_node(compose)
            self.assertEqual(self.wait_job(self.service, result['jobs'][0]['id'])['status'], 'succeeded')
        response = client.post('/api/studio/export', json={'project_id': 'p', 'clips': [{'url': 'http://127.0.0.1/secret'}]})
        self.assertEqual(response.status_code, 400)

    def test_openai_video_rejects_unsupported_timing_before_any_provider_request(self):
        from modules.generation_service import GenerationError
        service = self.configured(VIDEO_BASE_URL='https://provider.example/v1', VIDEO_API_KEY='video-test', VIDEO_MODEL='sora-test', VIDEO_PROTOCOL='openai')
        payload = self.payload('video-gen')
        payload['params']['durationSeconds'] = 5
        with patch.object(service, '_request_json', return_value={'id': 'blocked', 'status': 'failed'}):
            with self.assertRaises(GenerationError):
                service.submit_node(payload)

    def test_provider_response_total_time_is_bounded(self):
        from unittest.mock import MagicMock
        from modules.generation_service import GenerationError
        service = self.configured()
        session, response = MagicMock(), MagicMock()
        session.__enter__.return_value = session
        session.request.return_value = response
        response.status_code = 200
        response.headers = {'Content-Type': 'application/json'}
        response.iter_content.return_value = [b'{"data":[]}']
        with patch('modules.generation_service.requests.Session', return_value=session), patch('modules.generation_service.time.monotonic', side_effect=[0, 301]):
            with self.assertRaises(GenerationError) as error:
                service._request_json('image', '/images/generations', payload={})
        self.assertEqual(error.exception.status, 504)

    def test_audio_emotion_becomes_speech_instructions_and_legacy_tts_rejects_it(self):
        from modules.generation_service import GenerationError
        service = self.configured(AUDIO_BASE_URL='https://provider.example/v1', AUDIO_API_KEY='audio-test', AUDIO_MODEL='gpt-4o-mini-tts')
        payload = self.payload('audio-gen')
        payload['inputs'] = {'text': ['快来看看我们的新款商品']}
        payload['params'] = {'emotion': '轻松愉悦，热情但不夸张'}
        audio = b'RIFF' + b'\x00'*4 + b'WAVE' + b'\x00'*32
        with patch.object(service, '_request_binary', return_value=(audio, 'audio/wav')) as supplier:
            job = self.wait_job(service, service.submit_node(payload)['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded')
            self.assertEqual(supplier.call_args.kwargs['payload'].get('instructions'), payload['params']['emotion'])
        service.close()
        legacy = self.configured(AUDIO_BASE_URL='https://provider.example/v1', AUDIO_API_KEY='audio-test', AUDIO_MODEL='tts-1')
        with self.assertRaises(GenerationError):
            legacy.submit_node({**payload, 'client_run_id': 'legacy-run'})


if __name__ == '__main__':
    unittest.main()
