"""槐花MV 独立本地入口，保留原 React 画布和项目存储。"""
import argparse
import base64
import binascii
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from flask import Flask, jsonify, render_template, request, send_from_directory

from canvas_routes import canvas_bp
from config import Config
from modules.canvas_project_service import CanvasProjectService, CanvasProjectStorageError


def create_app(settings=None):
    app = Flask(__name__)
    app.config.from_mapping(DATABASE_PATH=Config.DATABASE_PATH, MAX_CONTENT_LENGTH=120 * 1024 * 1024)
    if settings:
        app.config.update(settings)
    app.extensions['canvas_storage'] = CanvasProjectService(app.config['DATABASE_PATH'])
    app.register_blueprint(canvas_bp)
    from studio_routes import studio_bp
    from modules.studio_service import StudioService
    from generation_routes import generation_bp
    from modules.generation_service import GenerationService
    app.extensions['studio_service'] = StudioService(app.config['DATABASE_PATH'])
    app.extensions['generation_service'] = GenerationService(app.config['DATABASE_PATH'], settings=app.config.get('GENERATION_SETTINGS'))
    app.register_blueprint(studio_bp)
    app.register_blueprint(generation_bp)

    @app.before_request
    def local_request():
        if urlsplit('http://' + request.host).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            return jsonify(success=False, error='画布仅支持本机访问'), 403
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('Origin')
            if (origin and origin != request.host_url.rstrip('/')) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return jsonify(success=False, error='禁止跨站写入本地画布'), 403
        if request.method in {'POST', 'PUT'} and request.path.startswith('/api/canvas/projects'):
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify(success=False, error='请求必须是 JSON 对象'), 400
            for field in ('nodes', 'edges'):
                if field in payload and not isinstance(payload[field], list):
                    return jsonify(success=False, error=f'{field} 必须是数组'), 400
            if 'viewport' in payload and not isinstance(payload['viewport'], dict):
                return jsonify(success=False, error='viewport 必须是对象'), 400
            if request.method == 'PUT' and request.view_args:
                project_id = request.view_args.get('project_id')
                if project_id:
                    try:
                        app.extensions['canvas_storage'].get_project(user_id='local-canvas-user', project_id=project_id)
                    except CanvasProjectStorageError:
                        return jsonify(success=False, error='画布不存在'), 404
        if request.method == 'GET' and request.path == '/api/canvas/projects':
            try:
                int(request.args.get('limit') or 30)
            except (TypeError, ValueError):
                return jsonify(success=False, error='limit 必须是整数'), 400

    @app.route('/')
    @app.route('/tools/canvas')
    @app.route('/tools/canvas/<project_id>')
    def canvas_page(project_id=None):
        if request.path == '/' and project_id is None:
            projects = app.extensions['canvas_storage'].list_projects(user_id='local-canvas-user', limit=1)
            project_id = projects[0]['id'] if projects else None
        return render_template(
            'canvas_app.html', user_id='local-canvas-user', username='槐花MV',
            user_credits=0, project_id=project_id,
            asset_version=str(int((Path(app.static_folder) / 'canvas/canvas-app.js').stat().st_mtime)),
        )

    @app.route('/health')
    def health():
        return jsonify(success=True, app='standalone-creative-canvas', module='creative-canvas', name='槐花MV')

    @app.route('/favicon.ico')
    def favicon():
        return '', 204

    @app.route('/api/canvas/assets', methods=['POST'])
    def upload_asset():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('image'), str):
            return jsonify(success=False, error='请提供图片 data URL'), 400
        header, separator, content = payload['image'].partition(',')
        extensions = {'data:image/png;base64': '.png', 'data:image/jpeg;base64': '.jpg', 'data:image/webp;base64': '.webp'}
        if not separator or header not in extensions:
            return jsonify(success=False, error='仅支持 PNG、JPEG、WebP 的 base64 图片'), 400
        try:
            decoded = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error):
            return jsonify(success=False, error='图片编码无效'), 400
        valid = ((header.startswith('data:image/png') and decoded.startswith(b'\x89PNG\r\n\x1a\n'))
                 or (header.startswith('data:image/jpeg') and decoded.startswith(b'\xff\xd8\xff'))
                 or (header.startswith('data:image/webp') and decoded.startswith(b'RIFF') and decoded[8:12] == b'WEBP'))
        if not valid or len(decoded) > 10 * 1024 * 1024:
            return jsonify(success=False, error='图片格式无效或大小超过 10 MB'), 400
        uploads = Path(app.config['DATABASE_PATH']).parent / 'uploads'
        uploads.mkdir(parents=True, exist_ok=True)
        filename = uuid.uuid4().hex + extensions[header]
        (uploads / filename).write_bytes(decoded)
        return jsonify(success=True, url='/canvas-assets/' + filename)

    @app.route('/canvas-assets/<filename>')
    def local_asset(filename):
        return send_from_directory(Path(app.config['DATABASE_PATH']).parent / 'uploads', filename)

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(success=False, error='请求超过 120 MB'), 413

    return app


def open_when_ready(url):
    for _ in range(50):
        try:
            with urlopen(url + 'health', timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='槐花MV · 节点式影像创作空间')
    parser.add_argument('--port', type=int, default=5088)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    application = create_app()
    if args.open:
        threading.Thread(target=open_when_ready, args=(f'http://127.0.0.1:{args.port}/',), daemon=True).start()
    application.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)
