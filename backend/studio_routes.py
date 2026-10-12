"""Production workspace API, sharing the canvas project's local identity/storage."""
import logging
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

from modules.canvas_project_service import CanvasProjectStorageError, CanvasProjectValidationError
from modules.studio_service import (
    StudioAssetNotFound, StudioUploadTooLarge, StudioValidationError,
    _number, _text, normalize_production,
)


studio_bp = Blueprint('studio', __name__)
logger = logging.getLogger(__name__)
USER_ID = 'local-canvas-user'


@studio_bp.before_request
def _local_access():
    if urlsplit('http://' + request.host).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        return jsonify(success=False, error='工作台仅支持本机访问'), 403
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        origin = request.headers.get('Origin')
        if (origin and origin != request.host_url.rstrip('/')) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(success=False, error='禁止跨站写入本地工作台'), 403


def _endpoint(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (StudioUploadTooLarge, RequestEntityTooLarge) as exc:
            return jsonify(success=False, error=str(exc) if isinstance(exc, StudioUploadTooLarge) else '素材请求超过大小限制'), 413
        except StudioAssetNotFound as exc:
            return jsonify(success=False, error=str(exc)), 404
        except (StudioValidationError, CanvasProjectValidationError) as exc:
            return jsonify(success=False, error=str(exc)), 400
        except CanvasProjectStorageError as exc:
            if '不存在' in str(exc) or '无权访问' in str(exc):
                return jsonify(success=False, error='作品不存在或无权访问'), 404
            logger.error('Studio project storage failed', exc_info=True)
            return jsonify(success=False, error='本地作品存储暂时不可用'), 503
        except Exception as exc:
            # Provider errors deliberately expose a sanitized message plus status.
            status = getattr(exc, 'status', None)
            if isinstance(status, int) and status in (400, 404, 409, 413, 422, 429, 502, 503, 504):
                return jsonify(success=False, error=str(exc)), status
            logger.error('Studio request failed', exc_info=True)
            return jsonify(success=False, error='工作台操作失败，请稍后重试'), 500
    return wrapped


def _services():
    return current_app.extensions['canvas_storage'], current_app.extensions['studio_service']


def _payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise StudioValidationError('请求必须是 JSON 对象')
    return data


def _production(data, base=None):
    if 'production' in data:
        value = data['production']
        # Null from a legacy canvas payload preserves an existing production.
        if value is None and base is not None:
            value = {}
    else:
        value = {key: data[key] for key in ('kind', 'brief', 'style', 'aspect_ratio', 'duration_seconds', 'script', 'characters', 'scenes', 'episodes', 'shots', 'timeline', 'voice_settings') if key in data}
    return normalize_production(value, base=base)


def _validate_canvas(data, existing, studio):
    nodes = data.get('nodes', existing.get('nodes', []))
    edges = data.get('edges', existing.get('edges', []))
    studio.validate_graph(nodes, edges)
    viewport = data.get('viewport', existing.get('viewport', {'x': 0, 'y': 0, 'zoom': 1}))
    if not isinstance(viewport, dict):
        raise StudioValidationError('viewport 必须是对象')
    return nodes, edges, {
        'x': _number(viewport.get('x', 0), 'viewport.x', minimum=-1000000, maximum=1000000),
        'y': _number(viewport.get('y', 0), 'viewport.y', minimum=-1000000, maximum=1000000),
        'zoom': _number(viewport.get('zoom', 1), 'viewport.zoom', minimum=0.01, maximum=10),
    }


@studio_bp.route('/api/studio/projects', methods=['GET'])
@_endpoint
def list_projects():
    storage, _ = _services()
    return jsonify(success=True, projects=storage.list_projects(user_id=USER_ID, limit=100))


@studio_bp.route('/api/studio/projects', methods=['POST'])
@_endpoint
def create_project():
    data = _payload()
    storage, studio = _services()
    production = _production(data)
    studio.validate_references(production)
    nodes, edges, viewport = _validate_canvas(data, {}, studio)
    title = _text(data.get('title', '未命名作品'), 'title', maximum=80)
    # POST always creates an independent work. Client IDs cannot overwrite one.
    project = storage.upsert_project(user_id=USER_ID, title=title, nodes=nodes, edges=edges, viewport=viewport, production=production)
    return jsonify(success=True, project=project)


@studio_bp.route('/api/studio/projects/<project_id>', methods=['GET'])
@_endpoint
def get_project(project_id):
    storage, _ = _services()
    return jsonify(success=True, project=storage.get_project(user_id=USER_ID, project_id=project_id))


@studio_bp.route('/api/studio/projects/<project_id>', methods=['PUT'])
@_endpoint
def update_project(project_id):
    data = _payload()
    storage, studio = _services()
    existing = storage.get_project(user_id=USER_ID, project_id=project_id)
    production = _production(data, base=existing.get('production'))
    studio.validate_references(production)
    nodes, edges, viewport = _validate_canvas(data, existing, studio)
    title = _text(data.get('title', existing['title']), 'title', maximum=80)
    project = storage.upsert_project(user_id=USER_ID, project_id=project_id, title=title, nodes=nodes, edges=edges, viewport=viewport, production=production)
    return jsonify(success=True, project=project)


@studio_bp.route('/api/studio/projects/<project_id>', methods=['DELETE'])
@_endpoint
def delete_project(project_id):
    storage, _ = _services()
    if not storage.delete_project(user_id=USER_ID, project_id=project_id):
        return jsonify(success=False, error='作品不存在或无权访问'), 404
    return jsonify(success=True)


@studio_bp.route('/api/studio/templates', methods=['GET'])
@_endpoint
def templates():
    _, studio = _services()
    return jsonify(success=True, templates=studio.templates())


@studio_bp.route('/api/studio/storyboard', methods=['POST'])
@_endpoint
def storyboard():
    payload = _payload()
    storage, studio = _services()
    source = payload.get('source', 'template')
    if source not in ('template', 'ai'):
        raise StudioValidationError('source 必须是 template 或 ai')
    if payload.get('project_id'):
        storage.get_project(user_id=USER_ID, project_id=payload['project_id'])
    production = normalize_production(payload.get('production'))
    # Unresolved local refs are input errors, not missing endpoint errors.
    try:
        studio.validate_references(production)
    except StudioAssetNotFound as exc:
        raise StudioValidationError(str(exc)) from exc
    if source == 'ai':
        service = current_app.extensions.get('generation_service')
        if service is None:
            return jsonify(success=False, error='尚未配置文字规划服务，请在本地配置供应商后使用 AI 分镜'), 503
        planned = service.plan_storyboard(production)
        # Accept complete production or the explicit {production: ...} wrapper.
        production = normalize_production(planned.get('production', planned) if isinstance(planned, dict) else planned, base=production)
    try:
        graph = studio.build_storyboard(production, source=source, episode_id=payload.get('episode_id'))
    except StudioAssetNotFound as exc:
        raise StudioValidationError(str(exc)) from exc
    return jsonify(success=True, **graph)


@studio_bp.route('/api/studio/assets', methods=['GET'])
@_endpoint
def assets():
    _, studio = _services()
    return jsonify(success=True, assets=studio.list_assets(category=request.args.get('category'), q=request.args.get('q', '')))


@studio_bp.route('/api/studio/assets', methods=['POST'])
@_endpoint
def upload_asset():
    _, studio = _services()
    asset = studio.add_asset(request.files.get('file'), name=request.form.get('name', ''), category=request.form.get('category', 'reference'))
    return jsonify(success=True, asset=asset)


@studio_bp.route('/api/studio/assets/<asset_id>', methods=['DELETE'])
@_endpoint
def delete_asset(asset_id):
    _, studio = _services()
    studio.delete_asset(asset_id)
    return jsonify(success=True)


@studio_bp.route('/studio-media/<filename>', methods=['GET'])
def media(filename):
    _, studio = _services()
    response = send_from_directory(studio.media_dir, filename, as_attachment=request.args.get('download') == '1')
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
