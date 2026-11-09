import base64
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import wave
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from flask import Flask

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1sAAAAASUVORK5CYII=')


def wav_bytes(frames=b'\0\x00' * 800, framerate=8000):
    import io
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, framerate, 0, 'NONE', 'not compressed'))
        wav.writeframes(frames)
    return buffer.getvalue()


class ExtractFrameTests(unittest.TestCase):
    def setUp(self):
        from modules.media_pipeline import MediaPipeline
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = Path(self.temp.name) / 'studio-media'
        self.media.mkdir()
        self.pipeline = MediaPipeline(self.media)
        self.clip = self.media / 'shot.mp4'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                        '-f', 'lavfi', '-i', 'color=c=green:s=160x90:r=12:d=1.2',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(self.clip)],
                       check=True, capture_output=True)

    def test_extracts_last_frame_as_image(self):
        from PIL import Image
        result = self.pipeline.extract_last_frame('/studio-media/shot.mp4')
        path = self.media / Path(result['url']).name
        self.assertTrue(path.is_file())
        self.assertEqual(result['kind'], 'image')
        with Image.open(path) as image:
            image.load()
            self.assertEqual((image.width, image.height), (160, 90))

    def test_rejects_non_video_media(self):
        (self.media / 'voice.wav').write_bytes(wav_bytes())
        from modules.media_pipeline import MediaError
        with self.assertRaises(MediaError) as caught:
            self.pipeline.extract_last_frame('/studio-media/voice.wav')
        self.assertIn('视频轨道', str(caught.exception))

    def test_failure_cleans_up_partial_output(self):
        broken = self.media / 'broken.mp4'
        broken.write_bytes(b'not-a-video')
        from modules.media_pipeline import MediaError
        with self.assertRaises(Exception):
            self.pipeline.extract_last_frame('/studio-media/broken.mp4')
        leftovers = [item for item in self.media.iterdir() if item.suffix == '.png']
        self.assertEqual(leftovers, [])

    def test_route_returns_frame_for_local_media(self):
        from app import create_app
        temp_app = tempfile.TemporaryDirectory()
        self.addCleanup(temp_app.cleanup)
        application = create_app({'DATABASE_PATH': str(Path(temp_app.name) / 'canvas.db'),
                                  'GENERATION_SETTINGS': {}})
        client = application.test_client()
        media_dir = Path(application.config['DATABASE_PATH']).parent / 'studio-media'
        media_dir.mkdir(exist_ok=True)
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                        '-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=8:d=0.8',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(media_dir / 'a.mp4')],
                       check=True, capture_output=True)
        response = client.post('/api/studio/extract-frame', json={'url': '/studio-media/a.mp4'})
        self.assertEqual(response.status_code, 200, response.get_json())
        frame = response.get_json()['frame']
        self.assertTrue((media_dir / Path(frame['url']).name).is_file())


class MusicServiceTests(unittest.TestCase):
    def setUp(self):
        from modules.music_service import MusicService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = Path(self.temp.name) / 'studio-media'
        self.media.mkdir()
        self.service = MusicService(self.media, Path(self.temp.name) / 'models', settings={})

    def test_status_and_availability(self):
        status = self.service.status()
        self.assertEqual(status['model'], 'ace-step')
        self.assertIsInstance(status['available'], bool)

    def test_missing_dependency_raises_actionable_error(self):
        from modules.media_pipeline import MediaError
        with patch.dict(sys.modules, {'acestep': None}):
            with self.assertRaises(MediaError) as caught:
                self.service.generate('轻快电商节奏')
        self.assertEqual(caught.exception.status, 400)
        self.assertIn('ACE-Step', str(caught.exception))

    def test_generate_collects_pipeline_output_and_cleans_workdir(self):
        produced = self.media / 'bgm-raw' / 'track.wav'
        produced.parent.mkdir()
        produced.write_bytes(wav_bytes())
        started = {item.resolve() for item in self.media.rglob('*') if item.is_file()}
        fake = unittest.mock.MagicMock(return_value={'audio_paths': [str(produced)]})
        with patch.object(self.service, '_load', return_value=fake):
            result = self.service.generate('轻快节奏', duration=30, instrumental=True)
        self.assertEqual(result['kind'], 'audio')
        saved = self.media / Path(result['url']).name
        self.assertTrue(saved.is_file())
        self.assertEqual(result['meta']['instrumental'], True)
        self.assertFalse(produced.exists(), '产出文件应被移动进素材目录')
        fake.assert_called_once()
        self.assertEqual(fake.call_args.kwargs['lyrics'], '[instrumental]')

    def test_generate_without_output_is_502(self):
        fake = unittest.mock.MagicMock(return_value={})
        with patch.object(self.service, '_load', return_value=fake):
            from modules.media_pipeline import MediaError
            with self.assertRaises(MediaError) as caught:
                self.service.generate('史漫剧配乐')
        self.assertEqual(caught.exception.status, 502)


class BgmGenerationTests(unittest.TestCase):
    def setUp(self):
        from modules.generation_service import GenerationService
        from modules.music_service import MusicService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'canvas.db'
        self.service = GenerationService(self.db, settings={})
        self.addCleanup(self.service.close)
        patcher = patch.object(MusicService, 'available', True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def payload(self, run='bgm-run', duration=30):
        return {'node_type': 'bgm-gen', 'node_id': 'bgm-one', 'project_id': 'p1',
                'client_run_id': run, 'params': {'durationSeconds': duration, 'instrumental': True},
                'inputs': {'prompt': ['轻快电商节奏，明快鼓点']}}

    def wait_job(self, job_id):
        for _ in range(200):
            job = self.service.get_job(job_id)
            if job['status'] in ('succeeded', 'failed', 'canceled'):
                return job
            time.sleep(.02)
        self.fail('job did not finish')

    def test_unavailable_reports_install_hint_as_400(self):
        from modules.generation_service import GenerationError
        from modules.music_service import MusicService
        with patch.object(MusicService, 'available', False):
            with self.assertRaises(GenerationError) as caught:
                self.service.submit_node(self.payload())
        self.assertEqual(caught.exception.status, 400)
        self.assertIn('ACE-Step', str(caught.exception))

    def test_invalid_duration_rejected(self):
        from modules.generation_service import GenerationError
        from modules.media_pipeline import MediaError
        with self.assertRaises((GenerationError, MediaError)) as caught:
            self.service.submit_node(self.payload(run='bgm-dur', duration=500))
        self.assertEqual(caught.exception.status, 400)

    def test_non_boolean_instrumental_rejected(self):
        from modules.generation_service import GenerationError
        payload = self.payload(run='bgm-inst')
        payload['params']['instrumental'] = 'yes'
        with self.assertRaises(GenerationError):
            self.service.submit_node(payload)

    def test_generate_job_produces_audio_and_caches(self):
        audio = wav_bytes()
        calls = {'count': 0}

        def fake_generate(**kwargs):
            calls['count'] += 1
            filename = 'bgm-%d.wav' % calls['count']
            (self.service.media_dir / filename).write_bytes(audio)
            return {'kind': 'audio', 'url': '/studio-media/' + filename,
                    'meta': {'tags': kwargs['tags'], 'duration_seconds': kwargs['duration']}}

        with patch.object(self.service.music, 'generate', side_effect=fake_generate):
            first = self.service.submit_node(self.payload(run='bgm-cache-a'))
            job = self.wait_job(first['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded', job)
            self.assertEqual(job['outputs'][0]['kind'], 'audio')
            second = self.service.submit_node(self.payload(run='bgm-cache-b'))
            second_job = self.service.get_job(second['jobs'][0]['id'])
            self.assertEqual(calls['count'], 1, '相同内容应命中内容缓存')
            self.assertEqual(second_job['status'], 'succeeded')
            self.assertTrue(second_job['provider_id'].startswith('cache:'))


class LipsyncTests(unittest.TestCase):
    def setUp(self):
        from modules.generation_service import GenerationService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'canvas.db'
        self.settings = {'LIPSYNC_BASE_URL': 'http://127.0.0.1:8020', 'LIPSYNC_POLL_INTERVAL': 0.01}
        self.service = GenerationService(self.db, settings={**self.settings})
        self.addCleanup(self.service.close)
        self.service.media_dir.mkdir(exist_ok=True)
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                        '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=8:d=0.8',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(self.service.media_dir / 'a.mp4')],
                       check=True, capture_output=True)
        (self.service.media_dir / 'voice.wav').write_bytes(wav_bytes())

    def payload(self, run='lip-run'):
        return {'node_type': 'lipsync', 'node_id': 'lip-one', 'project_id': 'p1',
                'client_run_id': run, 'params': {},
                'inputs': {'video': ['/studio-media/a.mp4'], 'audio': ['/studio-media/voice.wav']}}

    def wait_job(self, job_id):
        for _ in range(200):
            job = self.service.get_job(job_id)
            if job['status'] in ('succeeded', 'failed', 'canceled'):
                return job
            time.sleep(.02)
        self.fail('job did not finish')

    def test_provider_configuration_status(self):
        status = self.service.settings_status()
        self.assertIn('lipsync', status['providers'])
        self.assertTrue(status['providers']['lipsync']['configured'])

    def test_unconfigured_returns_503(self):
        from modules.generation_service import GenerationError, GenerationService
        bare = GenerationService(self.db, settings={})
        self.addCleanup(bare.close)
        with self.assertRaises(GenerationError) as caught:
            bare.submit_node(self.payload(run='lip-unconf'))
        self.assertEqual(caught.exception.status, 503)
        self.assertIn('LIPSYNC_BASE_URL', str(caught.exception))

    def test_missing_inputs_rejected(self):
        from modules.generation_service import GenerationError
        payload = self.payload(run='lip-missing')
        payload['inputs'].pop('audio')
        with self.assertRaises(GenerationError):
            self.service.submit_node(payload)

    def test_execute_polls_service_and_imports_result_video(self):
        produced = self.service.media_dir / 'lipsynced.mp4'
        shutil.copyfile(self.service.media_dir / 'a.mp4', produced)

        def fake_request_json(kind, path, **kwargs):
            if path == '/generate':
                self.assertEqual(kwargs['payload']['video_path'], str(self.service.media_dir / 'a.mp4'))
                self.assertEqual(kwargs['payload']['audio_path'], str(self.service.media_dir / 'voice.wav'))
                return {'job_id': 'latentsync-1'}
            if path.startswith('/status/'):
                return {'status': 'succeeded', 'video_path': str(produced)}
            raise AssertionError('unexpected path ' + path)

        with patch.object(self.service, '_request_json', side_effect=fake_request_json) as supplier:
            receipt = self.service.submit_node(self.payload())
            job = self.wait_job(receipt['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded', job)
            self.assertEqual(supplier.call_count, 2)
            self.assertEqual(job['outputs'][0]['kind'], 'video')
            imported = self.service.media_dir / Path(job['outputs'][0]['url']).name
            self.assertTrue(imported.is_file(), '结果视频应复制进素材目录')

    def test_service_failure_maps_to_502(self):
        def fake_request_json(kind, path, **kwargs):
            if path == '/generate':
                return {'job_id': 'latentsync-2'}
            return {'status': 'failed'}

        with patch.object(self.service, '_request_json', side_effect=fake_request_json):
            receipt = self.service.submit_node(self.payload(run='lip-fail'))
            job = self.wait_job(receipt['jobs'][0]['id'])
            self.assertEqual(job['status'], 'failed')
            self.assertIn('对口型服务任务失败', job['error'])

    def test_lipsync_content_is_cacheable(self):
        produced = self.service.media_dir / 'lipsynced2.mp4'
        shutil.copyfile(self.service.media_dir / 'a.mp4', produced)

        def fake_request_json(kind, path, **kwargs):
            if path == '/generate':
                return {'job_id': 'latentsync-3'}
            return {'status': 'succeeded', 'video_path': str(produced)}

        with patch.object(self.service, '_request_json', side_effect=fake_request_json):
            first = self.service.submit_node(self.payload(run='lip-cache-a'))
            self.wait_job(first['jobs'][0]['id'])
            second = self.service.submit_node(self.payload(run='lip-cache-b'))
            job = self.service.get_job(second['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded')
            self.assertTrue(job['provider_id'].startswith('cache:'))


class GraphRulesTests(unittest.TestCase):
    def test_bgm_and_lipsync_edges_validate(self):
        from modules.studio_service import StudioService
        nodes = [
            {'id': 'p', 'type': 'prompt', 'position': {'x': 0, 'y': 0}, 'data': {}},
            {'id': 'bgm', 'type': 'bgm-gen', 'position': {'x': 300, 'y': 0}, 'data': {}},
            {'id': 'av', 'type': 'audio-view', 'position': {'x': 600, 'y': 0}, 'data': {}},
            {'id': 'vv', 'type': 'video-view', 'position': {'x': 600, 'y': 300}, 'data': {}},
            {'id': 'lip', 'type': 'lipsync', 'position': {'x': 900, 'y': 300}, 'data': {}},
        ]
        edges = [
            {'id': 'e1', 'source': 'p', 'target': 'bgm', 'sourceHandle': 'out', 'targetHandle': 'in:prompt',
             'data': {'order': 1, 'mediaType': 'text'}},
            {'id': 'e2', 'source': 'bgm', 'target': 'av', 'sourceHandle': 'out', 'targetHandle': 'in:media',
             'data': {'order': 1, 'mediaType': 'audio'}},
            {'id': 'e3', 'source': 'vv', 'target': 'lip', 'sourceHandle': 'out', 'targetHandle': 'in:video',
             'data': {'order': 1, 'mediaType': 'video'}},
            {'id': 'e4', 'source': 'bgm', 'target': 'lip', 'sourceHandle': 'out', 'targetHandle': 'in:audio',
             'data': {'order': 2, 'mediaType': 'audio'}},
        ]
        StudioService.validate_graph(nodes, edges)

    def test_legacy_database_migration_still_works_with_new_kinds(self):
        from modules.generation_service import GenerationService
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Path(temp.name) / 'canvas.db'
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute('''CREATE TABLE generation_jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                node_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
                client_run_id TEXT UNIQUE NOT NULL, request_json TEXT NOT NULL,
                request_hash TEXT NOT NULL, outputs_json TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '', provider_id TEXT NOT NULL DEFAULT '',
                retry_of TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        service = GenerationService(db, settings={})
        self.addCleanup(service.close)
        self.assertIn('bgm-gen', service.KINDS)


if __name__ == '__main__':
    unittest.main()
