"""从原 app.py 提取的创意画布持久化接口。"""
import logging
from flask import Blueprint, current_app, jsonify, request
from werkzeug.local import LocalProxy
from modules.canvas_project_service import CanvasProjectStorageError, CanvasProjectValidationError

canvas_bp = Blueprint("canvas", __name__)
logger = logging.getLogger(__name__)
canvas_project_service = LocalProxy(lambda: current_app.extensions["canvas_storage"])

def _canvas_require_login():
    return "local-canvas-user", None, None

def _canvas_storage_response(exc):
    message = str(exc)
    if '不存在' in message or '无权访问' in message:
        return jsonify({'success': False, 'error': message}), 404
    if isinstance(exc, CanvasProjectValidationError) or '必须' in message:
        return jsonify({'success': False, 'error': message}), 400
    return jsonify({'success': False, 'error': message or '创意画布服务暂时不可用'}), 503


def _project_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise CanvasProjectValidationError('请求必须是 JSON 对象')
    for field in ('nodes', 'edges'):
        if field in payload and not isinstance(payload[field], list):
            raise CanvasProjectValidationError(f'{field} 必须是数组')
    if 'viewport' in payload and not isinstance(payload['viewport'], dict):
        raise CanvasProjectValidationError('viewport 必须是对象')
    if payload.get('production') is not None and not isinstance(payload['production'], dict):
        raise CanvasProjectValidationError('production 必须是对象')
    return payload


@canvas_bp.route('/api/canvas/projects', methods=['GET'])
def list_canvas_projects():
    """列出当前用户的创意画布项目"""
    user_id, error_response, status_code = _canvas_require_login()
    if error_response:
        return error_response, status_code

    try:
        try:
            limit = int(request.args.get('limit') or 30)
        except (TypeError, ValueError) as exc:
            raise CanvasProjectValidationError('limit 必须是整数') from exc
        projects = canvas_project_service.list_projects(user_id=user_id, limit=limit)
        return jsonify({'success': True, 'projects': projects})
    except CanvasProjectStorageError as exc:
        return _canvas_storage_response(exc)
    except Exception as exc:
        logger.error('读取创意画布列表失败: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': '读取创意画布列表失败，请稍后重试'}), 500


@canvas_bp.route('/api/canvas/projects', methods=['POST'])
def create_canvas_project():
    """保存一个新的创意画布项目"""
    user_id, error_response, status_code = _canvas_require_login()
    if error_response:
        return error_response, status_code

    try:
        payload = _project_payload()
        project = canvas_project_service.upsert_project(
            user_id=user_id,
            project_id=payload.get('id') or payload.get('project_id'),
            title=payload.get('title'),
            nodes=payload.get('nodes'),
            edges=payload.get('edges'),
            viewport=payload.get('viewport'),
            production=payload.get('production'),
        )
        return jsonify({'success': True, 'project': project})
    except CanvasProjectStorageError as exc:
        return _canvas_storage_response(exc)
    except Exception as exc:
        logger.error('保存创意画布失败: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': '保存创意画布失败，请稍后重试'}), 500


@canvas_bp.route('/api/canvas/projects/<project_id>', methods=['GET'])
def get_canvas_project(project_id):
    """读取单个创意画布项目"""
    user_id, error_response, status_code = _canvas_require_login()
    if error_response:
        return error_response, status_code

    try:
        project = canvas_project_service.get_project(user_id=user_id, project_id=project_id)
        return jsonify({'success': True, 'project': project})
    except CanvasProjectStorageError as exc:
        return _canvas_storage_response(exc)
    except Exception as exc:
        logger.error('读取创意画布失败: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': '读取创意画布失败，请稍后重试'}), 500


@canvas_bp.route('/api/canvas/projects/<project_id>', methods=['PUT'])
def update_canvas_project(project_id):
    """更新单个创意画布项目"""
    user_id, error_response, status_code = _canvas_require_login()
    if error_response:
        return error_response, status_code

    try:
        payload = _project_payload()
        canvas_project_service.get_project(user_id=user_id, project_id=project_id)
        project = canvas_project_service.upsert_project(
            user_id=user_id,
            project_id=project_id,
            title=payload.get('title'),
            nodes=payload.get('nodes'),
            edges=payload.get('edges'),
            viewport=payload.get('viewport'),
            production=payload.get('production'),
        )
        return jsonify({'success': True, 'project': project})
    except CanvasProjectStorageError as exc:
        return _canvas_storage_response(exc)
    except Exception as exc:
        logger.error('更新创意画布失败: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': '更新创意画布失败，请稍后重试'}), 500


@canvas_bp.route('/api/canvas/projects/<project_id>', methods=['DELETE'])
def delete_canvas_project(project_id):
    """删除单个创意画布项目"""
    user_id, error_response, status_code = _canvas_require_login()
    if error_response:
        return error_response, status_code

    try:
        deleted = canvas_project_service.delete_project(user_id=user_id, project_id=project_id)
        if not deleted:
            return jsonify({'success': False, 'error': '画布不存在或无权访问'}), 404
        return jsonify({'success': True})
    except CanvasProjectStorageError as exc:
        return _canvas_storage_response(exc)
    except Exception as exc:
        logger.error('删除创意画布失败: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': '删除创意画布失败，请稍后重试'}), 500
