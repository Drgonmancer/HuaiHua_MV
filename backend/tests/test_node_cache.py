import base64
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1sAAAAASUVORK5CYII=')


class NodeCacheTests(unittest.TestCase):
    def setUp(self):
        from modules.generation_service import GenerationService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'canvas.db'
        self.settings = {'IMAGE_BASE_URL': 'https://provider.example/v1', 'IMAGE_API_KEY': 'test-secret', 'IMAGE_MODEL': 'gpt-image-2'}
        self.service = GenerationService(self.db, settings={**self.settings})
        self.addCleanup(self.service.close)

    def payload(self, run='cache-run', prompt=None, **extra):
        payload = {'node_type': 'image-gen', 'node_id': 'node-one', 'project_id': 'project-one',
                   'client_run_id': run, 'params': {'count': 1, 'ratio': '1:1'}, 'inputs': {'prompt': [prompt or 'a red product']}}
        payload.update(extra)
        return payload

    def wait_job(self, service, job_id):
        for _ in range(150):
            job = service.get_job(job_id)
            if job['status'] in ('succeeded', 'failed', 'canceled'):
                return job
            time.sleep(.02)
        self.fail('background job did not finish')

    def provider_call(self, service):
        return patch.object(service, '_request_json', return_value={'data': [{'b64_json': base64.b64encode(PNG).decode()}]})

    def test_identical_content_is_served_from_cache(self):
        with self.provider_call(self.service) as supplier:
            first = self.service.submit_node(self.payload(run='cache-first'))
            first_job = self.wait_job(self.service, first['jobs'][0]['id'])
            self.assertEqual(supplier.call_count, 1)
            second = self.service.submit_node(self.payload(run='cache-second'))
            second_job = self.service.get_job(second['jobs'][0]['id'])
            self.assertEqual(supplier.call_count, 1, '相同内容不应再次请求供应商')
            self.assertNotEqual(first_job['id'], second_job['id'])
            self.assertEqual(second_job['status'], 'succeeded')
            self.assertEqual(second_job['outputs'], first_job['outputs'])
            self.assertTrue(second_job['provider_id'].startswith('cache:'))

    def test_cache_survives_restart(self):
        with self.provider_call(self.service):
            first = self.service.submit_node(self.payload(run='restart-first'))
            self.wait_job(self.service, first['jobs'][0]['id'])
        self.service.close()
        from modules.generation_service import GenerationService
        restored = GenerationService(self.db, settings={**self.settings})
        self.addCleanup(restored.close)
        with self.provider_call(restored) as supplier:
            second = restored.submit_node(self.payload(run='restart-second'))
            job = restored.get_job(second['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded')
            self.assertEqual(supplier.call_count, 0)

    def test_changed_content_bypasses_cache(self):
        with self.provider_call(self.service) as supplier:
            first = self.service.submit_node(self.payload(run='changed-a'))
            self.wait_job(self.service, first['jobs'][0]['id'])
            second = self.service.submit_node(self.payload(run='changed-b', prompt='a blue product'))
            job = self.wait_job(self.service, second['jobs'][0]['id'])
            self.assertEqual(supplier.call_count, 2)
            self.assertEqual(job['status'], 'succeeded')
            self.assertFalse(job['provider_id'].startswith('cache:'))

    def test_force_flag_bypasses_cache_for_regeneration(self):
        with self.provider_call(self.service) as supplier:
            first = self.service.submit_node(self.payload(run='force-a'))
            self.wait_job(self.service, first['jobs'][0]['id'])
            second = self.service.submit_node(self.payload(run='force-b', force=True))
            job = self.wait_job(self.service, second['jobs'][0]['id'])
            self.assertEqual(supplier.call_count, 2, '强制重跑必须发起新的生成请求')
            self.assertFalse(job['provider_id'].startswith('cache:'))

    def test_invalid_force_flag_is_rejected(self):
        from modules.generation_service import GenerationError
        with self.assertRaises(GenerationError) as caught:
            self.service.submit_node(self.payload(run='force-invalid', force='yes'))
        self.assertEqual(caught.exception.status, 400)

    def test_export_jobs_are_never_cached(self):
        captured = {'calls': 0}

        def fake_export(payload, cancel=None):
            captured['calls'] += 1
            return {'kind': 'video', 'url': '/studio-media/fake.mp4', 'meta': {}}

        self.service.media_dir.mkdir(exist_ok=True)
        (self.service.media_dir / 'a.mp4').write_bytes(b'clip')
        real = self.service.pipeline.export
        self.service.pipeline.export = fake_export
        try:
            for run in ('export-1', 'export-2'):
                job = self.service.submit_export({'project_id': 'p1', 'clips': [{'url': '/studio-media/a.mp4'}], 'client_run_id': run})
                self.wait_job(self.service, job['id'])
            self.assertEqual(captured['calls'], 2, '导出必须每次真实执行 FFmpeg')
        finally:
            self.service.pipeline.export = real

    def test_legacy_database_without_content_hash_column_is_migrated(self):
        self.service.close()
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute('DROP TABLE generation_jobs')
            connection.execute('''CREATE TABLE generation_jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                node_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
                client_run_id TEXT UNIQUE NOT NULL, request_json TEXT NOT NULL,
                request_hash TEXT NOT NULL, outputs_json TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '', provider_id TEXT NOT NULL DEFAULT '',
                retry_of TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        from modules.generation_service import GenerationService
        restored = GenerationService(self.db, settings={**self.settings})
        self.addCleanup(restored.close)
        with self.provider_call(restored):
            receipt = restored.submit_node(self.payload(run='legacy-run'))
            self.assertEqual(self.wait_job(restored, receipt['jobs'][0]['id'])['status'], 'succeeded')

    def test_idempotent_replay_still_returns_original_job(self):
        with self.provider_call(self.service) as supplier:
            first = self.service.submit_node(self.payload(run='same-run'))
            self.wait_job(self.service, first['jobs'][0]['id'])
            second = self.service.submit_node(self.payload(run='same-run'))
            self.assertEqual(first['jobs'], second['jobs'])
            self.assertEqual(supplier.call_count, 1)


if __name__ == '__main__':
    unittest.main()
