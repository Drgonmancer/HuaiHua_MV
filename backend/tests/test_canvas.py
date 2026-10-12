import importlib
import base64
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CanvasTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'app.py').exists(), '缺少创意画布本地入口')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {'TESTING': True, 'DATABASE_PATH': str(Path(self.temp.name) / 'canvas.db')}
        self.app = importlib.import_module('app').create_app(self.config)
        self.client = self.app.test_client()

    def test_correct_canvas_page_and_assets_load(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('canvas-root', response.text)
        self.assertIn('__CANVAS_BOOTSTRAP__', response.text)
        for path in ('/static/canvas/canvas-app.js', '/static/canvas/canvas-app.css'):
            with self.client.get(path) as asset:
                self.assertEqual(asset.status_code, 200)

    def test_node_graph_is_preserved_after_restarting_app(self):
        payload = {
            'title': '商品图视频工作流',
            'nodes': [
                {'id': 'prompt-1', 'type': 'prompt', 'position': {'x': 60, 'y': 80}, 'data': {'value': {'text': '商品主图'}}},
                {'id': 'image-1', 'type': 'image-gen', 'position': {'x': 350, 'y': 80}, 'data': {'params': {'model': 'gpt_image_2'}}},
            ],
            'edges': [{'id': 'e1', 'source': 'prompt-1', 'target': 'image-1', 'sourceHandle': 'out', 'targetHandle': 'in:prompt'}],
            'viewport': {'x': 12, 'y': 24, 'zoom': 0.8},
        }
        created = self.client.post('/api/canvas/projects', json=payload)
        self.assertEqual(created.status_code, 200)
        project = created.json['project']
        second_client = importlib.import_module('app').create_app(self.config).test_client()
        loaded = second_client.get('/api/canvas/projects/' + project['id']).json['project']
        for field in ('nodes', 'edges', 'viewport'):
            self.assertEqual(loaded[field], payload[field])
        self.assertEqual(loaded['schema_version'], 2)
        self.assertEqual(second_client.get('/tools/canvas/' + project['id']).status_code, 200)
        payload['title'] = '更新后的工作流'
        self.assertEqual(second_client.put('/api/canvas/projects/' + project['id'], json=payload).json['project']['title'], payload['title'])
        self.assertEqual(len(second_client.get('/api/canvas/projects').json['projects']), 1)
        self.assertEqual(second_client.delete('/api/canvas/projects/' + project['id']).status_code, 200)
        self.assertEqual(second_client.get('/api/canvas/projects/' + project['id']).status_code, 404)

    def test_invalid_requests_are_rejected(self):
        for value in ([], {'nodes': {}}, {'edges': {}}, {'viewport': []}):
            self.assertEqual(self.client.post('/api/canvas/projects', json=value).status_code, 400)
        self.assertEqual(self.client.post('/api/canvas/projects', json={}, headers={'Origin': 'https://example.com'}).status_code, 403)
        self.assertEqual(self.client.get('/', headers={'Host': 'example.com'}).status_code, 403)

    def test_paid_endpoints_are_explicitly_unconfigured(self):
        for path in ('/api/canvas/nodes/run', '/api/canvas/agent/plan', '/api/canvas/prompt-assist'):
            response = self.client.post(path, json={})
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json['success'])
        self.assertEqual(self.client.get('/api/video/models').json['models'], [])

    def test_uploaded_image_stays_local_and_rejects_other_content(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1sAAAAASUVORK5CYII=')
        payload = {'image': 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')}
        response = self.client.post('/api/canvas/assets', json=payload)
        self.assertEqual(response.status_code, 200)
        with self.client.get(response.json['url']) as asset:
            self.assertEqual(asset.data, png)
            self.assertEqual(asset.mimetype, 'image/png')
        for image in ('data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,YWJj', 'data:image/png;base64,!'):
            self.assertEqual(self.client.post('/api/canvas/assets', json={'image': image}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
