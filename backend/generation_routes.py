"""Local generation APIs. Media files are served by studio_bp."""
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from modules.generation_service import GenerationError
from modules.media_pipeline import MediaError
from modules.studio_service import StudioValidationError

generation_bp = Blueprint('generation', __name__)


@generation_bp.before_request
def _local_access():
    if urlsplit('http://' + request.host).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        return jsonify(success=False, error='生成服务仅支持本机访问'), 403
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        origin = request.headers.get('Origin')
        if (origin and origin != request.host_url.rstrip('/')) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(success=False, error='禁止跨站提交本地生成任务'), 403


def _endpoint(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (GenerationError, MediaError) as exc:
            return jsonify(success=False, error=str(exc)), exc.status
        except StudioValidationError as exc:
            return jsonify(success=False, error=str(exc)), 400
        except HTTPException as exc:
            return jsonify(success=False, error='请求格式无效或超过大小限制'), exc.code
        except Exception:
            # Do not log provider exceptions: even request URLs may be secret.
            return jsonify(success=False, error='本地生成服务暂时不可用，请稍后再试'), 500
    return wrapped


def _service():
    service = current_app.extensions.get('generation_service')
    if service is None:
        raise GenerationError('生成服务尚未初始化，请重启应用', 503)
    return service


def _payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise GenerationError('请求必须是 JSON 对象')
    return payload


def _project(project_id):
    """Check identity only when running as part of the full studio app."""
    storage = current_app.extensions.get('canvas_storage')
    if project_id and storage:
        from modules.canvas_project_service import CanvasProjectStorageError
        try:
            storage.get_project(user_id='local-canvas-user', project_id=project_id)
        except CanvasProjectStorageError:
            raise GenerationError('作品不存在或已删除', 404) from None


@generation_bp.route('/api/canvas/nodes/run', methods=['POST'])
@_endpoint
def run_node():
    payload = _payload()
    _project(payload.get('project_id'))
    return jsonify(success=True, **_service().submit_node(payload))


@generation_bp.route('/api/canvas/jobs/<kind>/<job_id>', methods=['GET'])
@_endpoint
def get_job(kind, job_id):
    job = _service().get_job(job_id, kind)
    # Keep executor's nested shape; expose fields at top level for older callers.
    return jsonify(success=True, job=job, status=job['status'], outputs=job['outputs'], error=job['error'])


@generation_bp.route('/api/canvas/runs/<client_run_id>', methods=['GET'])
@_endpoint
def run_receipt(client_run_id):
    return jsonify(success=True, **_service().get_run(client_run_id))


@generation_bp.route('/api/studio/jobs', methods=['GET'])
@_endpoint
def list_jobs():
    return jsonify(success=True, jobs=_service().list_jobs(request.args.get('project_id') or None))


@generation_bp.route('/api/studio/jobs/<job_id>/retry', methods=['POST'])
@_endpoint
def retry_job(job_id):
    _project(_service().get_job(job_id).get('project_id'))
    return jsonify(success=True, job=_service().retry_job(job_id))


@generation_bp.route('/api/studio/jobs/<job_id>/cancel', methods=['POST'])
@_endpoint
def cancel_job(job_id):
    return jsonify(success=True, job=_service().cancel_job(job_id))


@generation_bp.route('/api/studio/settings', methods=['GET'])
@_endpoint
def settings_status():
    return jsonify(success=True, **_service().settings_status())


@generation_bp.route('/api/studio/export', methods=['POST'])
@_endpoint
def export():
    payload = _payload()
    _project(payload.get('project_id'))
    return jsonify(success=True, job=_service().submit_export(payload))


@generation_bp.route('/api/canvas/agent/plan', methods=['POST'])
@_endpoint
def agent_plan():
    service = _service()
    service.require_provider('text')
    payload = _payload()
    _project(payload.get('project_id'))
    return jsonify(success=True, plan=service.agent_plan(payload.get('brief'), payload.get('production')), credits_cost=0)


@generation_bp.route('/api/canvas/prompt-assist', methods=['POST'])
@_endpoint
def prompt_assist():
    service = _service()
    service.require_provider('text')
    payload = _payload()
    _project(payload.get('project_id'))
    return jsonify(success=True, text=service.prompt_assist(payload.get('instruction'), payload.get('production')), credits_cost=0)


@generation_bp.route('/api/video/models', methods=['GET'])
@_endpoint
def video_models():
    return jsonify(success=True, models=_service().video_models())


@generation_bp.route('/api/canvas/audio-meta', methods=['GET'])
@_endpoint
def audio_meta():
    return jsonify(success=True, **_service().audio_meta())
