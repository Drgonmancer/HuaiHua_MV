"""Persistent local generation jobs and opt-in OpenAI/Seedance provider adapters.

Configuration is read from environment only when settings is None. Passing an
explicit settings dict isolates tests from the user's credentials. Submission
never retries a provider POST automatically: a lost result requires user retry.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import io
import ipaddress
import json
import mimetypes
import os
import re
import socket
import sqlite3
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from modules.media_pipeline import MediaError, MediaPipeline, number
from modules.studio_service import StudioService, StudioValidationError, normalize_production


class GenerationError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, field, maximum=32000, empty=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (not empty and not value.strip()):
        raise GenerationError(f'{field} 必须是有效文本（最多 {maximum} 字）')
    return value.strip()


def _identifier(value, field, empty=False):
    value = _text(value, field, 128, empty)
    if value and not re.fullmatch(r'[\w.:-]+', value):
        raise GenerationError(f'{field} 格式无效')
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False)


def _extension(data, kind, content_type=''):
    """Allow recognized media containers only; never persist HTML/SVG as media."""
    if kind == 'image':
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            suffix = '.png'
        elif data.startswith(b'\xff\xd8\xff'):
            suffix = '.jpg'
        elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
            suffix = '.webp'
        else:
            raise GenerationError('供应商返回的图片格式无效', 502)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 40_000_000:
                    raise GenerationError('供应商返回的图片超过像素限制', 502)
                image.load()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise GenerationError('供应商返回的图片无法解码', 502) from None
        return suffix
    if kind == 'video':
        if len(data) >= 12 and data[4:8] == b'ftyp':
            return '.mp4'
        if data.startswith(b'\x1aE\xdf\xa3'):
            return '.webm'
    if kind == 'audio':
        if data.startswith(b'RIFF') and data[8:12] == b'WAVE':
            return '.wav'
        if data.startswith(b'ID3') or (len(data) > 1 and data[0] == 255 and data[1] & 224 == 224):
            return '.mp3'
        if data.startswith(b'OggS'):
            return '.ogg'
        if data.startswith(b'fLaC'):
            return '.flac'
        if len(data) >= 12 and data[4:8] == b'ftyp':
            return '.m4a'
    raise GenerationError('供应商返回的媒体格式无效', 502)


class PublicMediaDownloader:
    """Resolve every redirect and pin sockets to validated public IP addresses.

    DNS is resolved once for each hop; TLS certificate checks still use the
    original hostname. This prevents both redirect SSRF and DNS rebinding.
    No provider Authorization header is forwarded to result/CDN URLs.
    """
    def __init__(self, media_dir, max_bytes=100 * 1024 * 1024):
        self.media_dir = Path(media_dir)
        self.max_bytes = max_bytes

    @staticmethod
    def _resolve(url):
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                    or parsed.fragment or parsed.port not in (None, 80, 443) or len(url) > 8192):
                raise ValueError()
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ValueError()
            return parsed, port, addresses[0][4][0]
        except (ValueError, OSError, TypeError):
            raise GenerationError('媒体下载地址不是允许的公网 HTTP 地址', 502) from None

    def _open(self, url, resolved):
        parsed, port, address = resolved
        if parsed.scheme == 'https':
            connection = http.client.HTTPSConnection(parsed.hostname, port, timeout=30, context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(parsed.hostname, port, timeout=30)
        # HTTPConnection.connect calls this once. HTTPS wraps the pinned socket
        # with server_hostname=self.host, preserving hostname verification.
        connection._create_connection = lambda _target, timeout=30, source_address=None: socket.create_connection((address, port), timeout, source_address)
        try:
            connection.request('GET', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''),
                               headers={'Accept': 'image/*,video/*,audio/*', 'User-Agent': 'LocalCreativeStudio/1.0'})
            response = connection.getresponse()
            response._studio_connection = connection
            return response.status, {key.lower(): value for key, value in response.getheaders()}, response
        except Exception:
            connection.close()
            raise GenerationError('供应商媒体下载失败', 502) from None

    def download(self, url, kind, cancel=None):
        started = time.monotonic()
        for _ in range(5):
            resolved = self._resolve(url)
            response = None
            try:
                status, headers, response = self._open(url, resolved)
                if status in (301, 302, 303, 307, 308):
                    location = headers.get('location')
                    if not location:
                        raise GenerationError('供应商媒体重定向缺少目标', 502)
                    url = urljoin(url, location)
                    continue
                if status != 200:
                    raise GenerationError('供应商媒体下载失败', 502)
                content_type = headers.get('content-type', '').split(';')[0].lower()
                if not content_type.startswith(kind + '/') or content_type == 'image/svg+xml':
                    raise GenerationError('供应商媒体 Content-Type 无效', 502)
                try:
                    if int(headers.get('content-length', '0')) > self.max_bytes:
                        raise GenerationError('供应商媒体超过大小限制', 502)
                except ValueError:
                    raise GenerationError('供应商媒体长度无效', 502) from None
                data = bytearray()
                while True:
                    if cancel and cancel.is_set():
                        raise GenerationError('任务已取消', 409)
                    if time.monotonic() - started > 180:
                        raise GenerationError('供应商媒体下载超时', 504)
                    chunk = response.read(min(65536, self.max_bytes + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > self.max_bytes:
                        raise GenerationError('供应商媒体超过大小限制', 502)
                suffix = _extension(data, kind, content_type)
                filename = uuid.uuid4().hex + suffix
                (self.media_dir / filename).write_bytes(data)
                return {'kind': kind, 'url': '/studio-media/' + filename}
            except GenerationError:
                raise
            except (OSError, http.client.HTTPException):
                raise GenerationError('供应商媒体下载失败', 502) from None
            finally:
                if response:
                    response.close()
                    connection = getattr(response, '_studio_connection', None)
                    if connection:
                        connection.close()
        raise GenerationError('供应商媒体重定向次数过多', 502)


class GenerationService:
    KINDS = {'image-gen': 'image', 'video-gen': 'video', 'audio-gen': 'audio', 'compose': 'compose'}

    def __init__(self, db_path, settings=None):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_dir = self.db_path.parent / 'studio-media'
        self.media_dir.mkdir(exist_ok=True)
        self.settings = dict(os.environ if settings is None else settings)
        self.pipeline = MediaPipeline(self.media_dir)
        self.downloader = PublicMediaDownloader(self.media_dir)
        self.providers = {kind: self._provider(kind) for kind in ('text', 'image', 'video', 'audio')}
        self._lock = threading.RLock()
        self._cancel = {}
        self._futures = {}
        self._closed = False
        workers = int(number(self.settings.get('MAX_WORKERS') or 2, 'MAX_WORKERS', 1, 8))
        pending = int(number(self.settings.get('MAX_PENDING') or 16, 'MAX_PENDING', 1, 128))
        self._slots = threading.BoundedSemaphore(pending)
        self._text_slots = threading.BoundedSemaphore(2)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='studio-generation')
        with self._connection() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS generation_jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                node_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
                client_run_id TEXT UNIQUE NOT NULL, request_json TEXT NOT NULL,
                request_hash TEXT NOT NULL, outputs_json TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '', provider_id TEXT NOT NULL DEFAULT '',
                retry_of TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            connection.execute('CREATE INDEX IF NOT EXISTS idx_generation_project ON generation_jobs(project_id, created_at DESC)')
            # Deliberately do not resubmit interrupted jobs; the supplier may have
            # already accepted/billed a POST whose result was never persisted.
            connection.execute("UPDATE generation_jobs SET status='failed',error=?,updated_at=? WHERE status IN ('queued','running')",
                               ('应用重启时任务中断；请确认供应商状态后手动重试，重试可能产生新的供应商费用', _now()))

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _provider(self, kind):
        prefix = kind.upper()
        read = lambda name: str(self.settings.get(prefix + '_' + name) or '').strip()
        base = read('BASE_URL')
        key, model = read('API_KEY'), read('MODEL')
        protocol = read('PROTOCOL') or ('seedance' if kind == 'video' else 'openai')
        configured = bool(base and key and model)
        if configured:
            parsed = urlsplit(base)
            # Provider URLs are explicitly configured by the local user, so
            # loopback relays are allowed. Credentials in URL are forbidden.
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                configured = False
            if protocol not in ('openai', 'seedance') or (kind != 'video' and protocol != 'openai'):
                configured = False
        models = [entry.strip() for entry in (read('MODELS') or model).split(',') if entry.strip()]
        return {'base_url': base.rstrip('/'), 'key': key, 'model': model, 'protocol': protocol,
                'configured': configured, 'models': list(dict.fromkeys([model] + models)) if model else []}

    def require_provider(self, kind):
        if not self.providers[kind]['configured']:
            label = {'text': '文字规划', 'image': '图片生成', 'video': '视频生成', 'audio': '语音生成'}[kind]
            raise GenerationError(f'尚未配置{label}供应商，请在本地 .env 中设置 {kind.upper()}_BASE_URL、{kind.upper()}_API_KEY 和 {kind.upper()}_MODEL 后重启', 503)
        return self.providers[kind]

    def image_models(self):
        provider = self.providers['image']
        return [{'id': model, 'label': model, 'credits_cost': 0} for model in provider['models']] if provider['configured'] else []

    def video_models(self):
        provider = self.providers['video']
        return [{'public_model_id': model, 'name': model, 'enabled': True,
                 'supported_modes': ['t2v', 'i2v'], 'aspect_ratios': ['16:9', '9:16'] if provider['protocol'] == 'openai' else ['16:9', '9:16', '1:1'],
                 'verified_durations': [4, 8, 12] if provider['protocol'] == 'openai' else [5, 10],
                 'resolution': '720p', 'supported_resolutions': ['720p'] if provider['protocol'] == 'openai' else ['720p', '1080p'],
                 'supports_end_image': provider['protocol'] == 'seedance', 'credits_cost': 0, 'credits_per_second': 0}
                for model in provider['models']] if provider['configured'] else []

    def audio_meta(self):
        provider = self.providers['audio']
        voices = str(self.settings.get('AUDIO_VOICES') or 'alloy,echo,fable,onyx,nova,shimmer').split(',')
        return {'configured': provider['configured'],
                'models': [{'id': model, 'label': model, 'name': model, 'credits_per_100_chars': 0} for model in provider['models']] if provider['configured'] else [],
                'voices': [{'id': voice.strip(), 'label': voice.strip(), 'name': voice.strip()} for voice in voices if voice.strip()] if provider['configured'] else []}

    def settings_status(self):
        public = {kind: {'configured': provider['configured'], 'model': provider['model'],
                         'models': provider['models'] if provider['configured'] else []}
                  for kind, provider in self.providers.items()}
        return {'providers': public, 'ffmpeg': {'available': self.pipeline.available},
                'config_path': str(Path(__file__).resolve().parents[2] / '.env'),
                'image_models': self.image_models(), 'video_models': self.video_models(), 'audio': self.audio_meta()}

    @staticmethod
    def _job(row):
        return {key: row[key] for key in ('id', 'kind', 'status', 'node_id', 'project_id', 'error', 'provider_id', 'retry_of', 'created_at', 'updated_at')} | {'outputs': json.loads(row['outputs_json'])}

    def get_job(self, job_id, kind=None):
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM generation_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None or (kind is not None and kind != row['kind']):
            raise GenerationError('任务不存在', 404)
        return self._job(row)

    def get_run(self, client_run_id):
        _identifier(client_run_id, 'client_run_id')
        with self._connection() as connection:
            row = connection.execute('SELECT id,kind FROM generation_jobs WHERE client_run_id=?', (client_run_id,)).fetchone()
        if row is None:
            raise GenerationError('任务提交记录尚未确认', 404)
        return {'jobs': [{'id': row['id'], 'kind': row['kind']}], 'credits_cost': 0}

    def list_jobs(self, project_id=None):
        if project_id:
            _identifier(project_id, 'project_id')
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM generation_jobs WHERE (? IS NULL OR project_id=?) ORDER BY created_at DESC LIMIT 200', (project_id, project_id)).fetchall()
        return [self._job(row) for row in rows]

    def _selected_model(self, kind, params):
        provider = self.require_provider(kind)
        model = params.get('model')
        if model is not None and not isinstance(model, str):
            raise GenerationError('model 必须是字符串')
        if model in provider['models']:
            return model
        # Existing graphs use legacy public IDs. Resolve these to local model
        # config; never send an arbitrary unlisted upstream model name.
        legacy = {'image': {'gpt_image_2', 'gpt-image-2', 'flux_dev', 'flux_schnell'},
                  'video': {'seedance_2_0', 'seedance_2_0_fast', 'seedance_1_5_pro'},
                  'audio': {'tts-1', 'gpt-4o-mini-tts'}}
        if not model or model in legacy.get(kind, set()):
            return provider['model']
        if not isinstance(model, str) or model not in provider['models']:
            raise GenerationError('所选模型未在本地供应商目录中配置')
        return model

    def _validate_node(self, payload):
        if not isinstance(payload, dict):
            raise GenerationError('请求必须是 JSON 对象')
        node_type = payload.get('node_type')
        if not isinstance(node_type, str) or node_type not in self.KINDS:
            if not any(self.providers[kind]['configured'] for kind in ('image', 'video', 'audio')):
                raise GenerationError('尚未配置生成供应商，请先在本地 .env 中配置', 503)
            raise GenerationError('node_type 必须是 image-gen、video-gen、audio-gen 或 compose')
        kind = self.KINDS[node_type]
        if kind != 'compose':
            self.require_provider(kind)
        params, inputs = payload.get('params', {}), payload.get('inputs', {})
        if not isinstance(params, dict) or not isinstance(inputs, dict):
            raise GenerationError('params 和 inputs 必须是对象')
        allowed = {'image': {'prompt': 4, 'ref': 6}, 'video': {'prompt': 2, 'first': 1, 'last': 1},
                   'audio': {'text': 1}, 'compose': {'video': 1, 'audio': 2}}[kind]
        for name, values in inputs.items():
            if name not in allowed or not isinstance(values, list) or len(values) > allowed[name]:
                raise GenerationError('输入端口或输入数量无效')
            for value in values:
                _text(value, name)
                if name in ('ref', 'first', 'last', 'video', 'audio'):
                    self.pipeline.resolve_media(value)
                if name in ('ref', 'first', 'last'):
                    self._image_data(value)
        params = dict(params)
        if kind != 'compose':
            params['model'] = self._selected_model(kind, params)
            text_key = 'text' if kind == 'audio' else 'prompt'
            if not inputs.get(text_key):
                raise GenerationError('请连接文本或提示词输入')
            _text('\n'.join(inputs[text_key]), text_key, 4000 if kind == 'audio' else 32000)
        if kind == 'image':
            count = number(params.get('count', 1), '图片数量', 1, 4)
            if int(count) != count:
                raise GenerationError('图片数量必须是整数')
            params['count'] = int(count)
            ratio = params.get('ratio') or params.get('aspectRatio') or '1:1'
            if ratio not in ('1:1', '16:9', '9:16', '4:3', '3:4'):
                raise GenerationError('图片比例不支持')
            params['ratio'] = ratio
        if kind == 'video':
            protocol = self.providers['video']['protocol']
            duration = number(params.get('durationSeconds', 4 if protocol == 'openai' else 5), '视频时长', 1, 30)
            if int(duration) != duration or (protocol == 'openai' and duration not in (4, 8, 12)):
                raise GenerationError('OpenAI 视频时长支持 4、8、12 秒；Seedance 时长必须为整数')
            params['durationSeconds'] = int(duration)
            ratio = params.get('ratio') or params.get('aspectRatio') or '16:9'
            if ratio not in ('1:1', '16:9', '9:16', '4:3', '3:4', '21:9', 'adaptive'):
                raise GenerationError('视频比例不支持')
            params['ratio'] = ratio
            if params.get('resolution', '720p') not in ('480p', '720p', '1080p'):
                raise GenerationError('视频分辨率不支持')
            if protocol == 'openai' and (ratio not in ('16:9', '9:16') or params.get('resolution', '720p') != '720p'):
                raise GenerationError('当前 OpenAI 视频适配支持 720p 的 16:9 或 9:16 画幅')
            if inputs.get('last') and self.providers['video']['protocol'] != 'seedance':
                raise GenerationError('当前 OpenAI 视频协议不支持尾帧输入')
        if kind == 'audio':
            params['speed'] = number(params.get('speed', 1), '语速', .25, 4)
            params['emotion'] = _text(params.get('emotion') or '', '配音情绪', 500, True)
            if params['emotion'] and params['model'] in ('tts-1', 'tts-1-hd'):
                raise GenerationError('tts-1 系列不支持情绪指令，请清空情绪或选择支持 instructions 的配音模型')
            voice = params.get('voiceId') or params.get('voice_id') or self.audio_meta()['voices'][0]['id']
            if voice not in {entry['id'] for entry in self.audio_meta()['voices']}:
                raise GenerationError('音色未在本地 AUDIO_VOICES 中配置')
            params['voiceId'] = voice
        if kind == 'compose':
            self.pipeline.validate_export({'clips': [{'url': url, 'subtitle': params.get('subtitle', '')} for url in inputs.get('video', [])],
                                           'audio_urls': inputs.get('audio', []), 'resolution': params.get('resolution', '720p'), 'aspect_ratio': params.get('ratio', '16:9')})
            if not self.pipeline.available:
                raise GenerationError('FFmpeg 未安装，暂时无法合成', 503)
        return kind, {'node_type': node_type, 'node_id': _identifier(payload.get('node_id'), 'node_id'),
                      'project_id': _identifier(payload.get('project_id') or '', 'project_id', True),
                      'client_run_id': _identifier(payload.get('client_run_id'), 'client_run_id'),
                      'params': params, 'inputs': inputs}

    def submit_node(self, payload):
        # Fingerprint the original request before selecting today's provider
        # model so replay remains stable across configuration changes.
        if isinstance(payload, dict) and isinstance(payload.get('client_run_id'), str):
            replay = self._replay(payload['client_run_id'], payload)
            if replay:
                return {'jobs': [{'kind': replay['kind'], 'id': replay['id']}], 'credits_cost': 0}
        kind, normalized = self._validate_node(payload)
        job = self._submit(kind, normalized, original=payload)
        return {'jobs': [{'kind': kind, 'id': job['id']}], 'credits_cost': 0}

    def _replay(self, client_run_id, original):
        try:
            digest = hashlib.sha256(_json(original).encode()).hexdigest()
        except (TypeError, ValueError):
            raise GenerationError('请求包含无效 JSON 参数') from None
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM generation_jobs WHERE client_run_id=?', (client_run_id,)).fetchone()
        if row:
            if row['request_hash'] != digest:
                raise GenerationError('client_run_id 已被不同请求使用，请创建新的运行 ID', 409)
            return self._job(row)
        return None

    def _submit(self, kind, payload, original=None, retry_of=''):
        original = original if original is not None else payload
        with self._lock:
            replay = self._replay(payload['client_run_id'], original)
            if replay:
                return replay
            if self._closed:
                raise GenerationError('任务执行器已关闭，请重启应用', 503)
            if not self._slots.acquire(blocking=False):
                raise GenerationError('正在处理的任务过多，请稍后再试', 429)
            job_id, stamp = uuid.uuid4().hex, _now()
            cancel = threading.Event()
            try:
                with self._connection() as connection:
                    connection.execute('''INSERT INTO generation_jobs(id,kind,status,node_id,project_id,client_run_id,request_json,request_hash,outputs_json,error,provider_id,retry_of,created_at,updated_at)
                                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                       (job_id, kind, 'queued', payload.get('node_id', ''), payload.get('project_id', ''), payload['client_run_id'],
                                        _json(payload), hashlib.sha256(_json(original).encode()).hexdigest(), '[]', '', '', retry_of, stamp, stamp))
                self._cancel[job_id] = cancel
                future = self._executor.submit(self._execute, job_id, kind, payload, cancel)
                self._futures[job_id] = future
                future.add_done_callback(lambda _future: self._release(job_id))
            except Exception:
                self._slots.release()
                raise
        return self.get_job(job_id)

    def _release(self, job_id):
        with self._lock:
            self._futures.pop(job_id, None)
            self._cancel.pop(job_id, None)
            self._slots.release()

    def _execute(self, job_id, kind, payload, cancel):
        with self._connection() as connection:
            changed = connection.execute("UPDATE generation_jobs SET status='running',updated_at=? WHERE id=? AND status='queued'", (_now(), job_id)).rowcount
        if not changed or cancel.is_set():
            return
        try:
            if kind == 'image':
                outputs = self._image(payload, cancel)
            elif kind == 'audio':
                outputs = self._audio(payload, cancel)
            elif kind == 'video':
                outputs = self._video(job_id, payload, cancel)
            else:
                export = payload if kind == 'export' else {
                    'clips': [{'url': url, 'subtitle': payload['params'].get('subtitle', '')} for url in payload['inputs']['video']],
                    'audio_urls': payload['inputs'].get('audio', []), 'resolution': payload['params'].get('resolution', '720p'),
                    'aspect_ratio': payload['params'].get('ratio', '16:9')}
                outputs = [self.pipeline.export(export, cancel)]
            with self._connection() as connection:
                connection.execute("UPDATE generation_jobs SET status='succeeded',outputs_json=?,updated_at=? WHERE id=? AND status='running'", (_json(outputs), _now(), job_id))
            if cancel.is_set():
                for output in outputs:
                    try:
                        self.pipeline.resolve_media(output['url']).unlink(missing_ok=True)
                    except (MediaError, KeyError):
                        pass
        except Exception as exc:
            # Never persist raw provider messages, response bodies, request URLs
            # or exception strings that could contain Authorization credentials.
            message = str(exc) if isinstance(exc, (GenerationError, MediaError)) else '生成服务请求失败，请检查供应商配置或稍后手动重试'
            for provider in self.providers.values():
                if provider['key']:
                    message = message.replace(provider['key'], '[redacted]')
            with self._connection() as connection:
                connection.execute("UPDATE generation_jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='running'", (message, _now(), job_id))

    def cancel_job(self, job_id):
        job = self.get_job(job_id)
        if job['status'] in ('succeeded', 'failed', 'canceled'):
            return job
        with self._lock:
            if job_id in self._cancel:
                self._cancel[job_id].set()
            with self._connection() as connection:
                connection.execute("UPDATE generation_jobs SET status='canceled',error=?,updated_at=? WHERE id=? AND status IN ('queued','running')",
                                   ('已停止本地处理；已提交给供应商的任务可能继续执行并计费', _now(), job_id))
            if job_id in self._futures:
                self._futures[job_id].cancel()
        return self.get_job(job_id)

    def retry_job(self, job_id):
        job = self.get_job(job_id)
        if job['status'] not in ('failed', 'canceled'):
            raise GenerationError('只有失败或取消的任务可以重试', 409)
        with self._connection() as connection:
            stored = connection.execute('SELECT request_json FROM generation_jobs WHERE id=?', (job_id,)).fetchone()
        payload = json.loads(stored['request_json'])
        payload['client_run_id'] = 'retry-' + uuid.uuid4().hex
        if job['kind'] == 'export':
            self.pipeline.validate_export(payload)
        else:
            _, payload = self._validate_node(payload)
        return self._submit(job['kind'], payload, retry_of=job_id)

    def submit_export(self, payload):
        self.pipeline.validate_export(payload)
        if not self.pipeline.available:
            raise GenerationError('FFmpeg 未安装，暂时无法导出', 503)
        normalized = {key: payload[key] for key in ('clips', 'audio_urls', 'aspect_ratio', 'resolution', 'fps') if key in payload}
        normalized.update(project_id=_identifier(payload.get('project_id'), 'project_id'), node_id='',
                          client_run_id=_identifier(payload.get('client_run_id') or 'export-' + uuid.uuid4().hex, 'client_run_id'))
        return self._submit('export', normalized)

    def close(self):
        if self._closed:
            return
        self._closed = True
        for event in list(self._cancel.values()):
            event.set()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _request(self, kind, path, *, payload=None, method='POST', files=None):
        provider = self.require_provider(kind)
        started = time.monotonic()
        try:
            with requests.Session() as session:
                session.trust_env = False
                kwargs = {'headers': {'Authorization': 'Bearer ' + provider['key']}, 'timeout': (10, 180),
                          'allow_redirects': False, 'stream': True}
                if files:
                    kwargs.update(data={key: str(value) for key, value in (payload or {}).items()}, files=files)
                elif payload is not None:
                    kwargs['json'] = payload
                response = session.request(method, provider['base_url'] + path, **kwargs)
                try:
                    if not 200 <= response.status_code < 300:
                        raise GenerationError(f'供应商请求失败（HTTP {response.status_code}），请检查本地配置', 502)
                    maximum = 100 * 1024 * 1024 if '/content' in path or kind == 'audio' else 32 * 1024 * 1024
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        if time.monotonic() - started > 300:
                            raise GenerationError('供应商响应超过五分钟，请检查任务状态后手动重试', 504)
                        size += len(chunk)
                        if size > maximum:
                            raise GenerationError('供应商响应超过大小限制', 502)
                        chunks.append(chunk)
                    return b''.join(chunks), response.headers.get('Content-Type', '')
                finally:
                    response.close()
        except GenerationError:
            raise
        except requests.RequestException:
            raise GenerationError('供应商连接失败或响应超时，请检查本地配置', 502) from None

    def _request_json(self, kind, path, **kwargs):
        data, _ = self._request(kind, path, **kwargs)
        try:
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeError):
            raise GenerationError('供应商返回的 JSON 格式无效', 502) from None

    def _request_binary(self, kind, path, **kwargs):
        return self._request(kind, path, **kwargs)

    def _save(self, data, kind, content_type=''):
        if len(data) > 100 * 1024 * 1024:
            raise GenerationError('供应商媒体超过大小限制', 502)
        suffix = _extension(data, kind, content_type)
        filename = uuid.uuid4().hex + suffix
        (self.media_dir / filename).write_bytes(data)
        return {'kind': kind, 'url': '/studio-media/' + filename}

    def _image_data(self, url):
        path = self.pipeline.resolve_media(url)
        data = path.read_bytes()
        if len(data) > 20 * 1024 * 1024:
            raise GenerationError('参考图片超过 20 MB')
        suffix = _extension(data, 'image')
        mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp'}[suffix]
        return data, mime, path.name

    def _image(self, payload, cancel):
        params, inputs = payload['params'], payload['inputs']
        sizes = {'1:1': '1024x1024', '16:9': '1536x1024', '4:3': '1536x1024', '9:16': '1024x1536', '3:4': '1024x1536'}
        request = {'model': params['model'], 'prompt': '\n'.join(inputs['prompt']), 'n': params['count'], 'size': sizes[params['ratio']]}
        references = inputs.get('ref', [])
        if references:
            files = []
            for reference in references:
                data, mime, filename = self._image_data(reference)
                files.append(('image[]', (filename, data, mime)))
            result = self._request_json('image', '/images/edits', payload=request, files=files)
        else:
            result = self._request_json('image', '/images/generations', payload=request)
        entries = result.get('data')
        if not isinstance(entries, list) or not entries or len(entries) > 4:
            raise GenerationError('供应商没有返回有效图片', 502)
        outputs = []
        for entry in entries:
            if cancel.is_set():
                raise GenerationError('任务已取消', 409)
            if isinstance(entry, dict) and isinstance(entry.get('b64_json'), str):
                try:
                    data = base64.b64decode(entry['b64_json'], validate=True)
                except (binascii.Error, ValueError):
                    raise GenerationError('供应商图片编码无效', 502) from None
                outputs.append(self._save(data, 'image'))
            elif isinstance(entry, dict) and isinstance(entry.get('url'), str):
                outputs.append(self.downloader.download(entry['url'], 'image', cancel))
            else:
                raise GenerationError('供应商图片数据无效', 502)
        return outputs

    def _audio(self, payload, cancel):
        params = payload['params']
        request = {'model': params['model'], 'input': '\n'.join(payload['inputs']['text']),
                   'voice': params['voiceId'], 'speed': params['speed'], 'response_format': 'mp3'}
        if params.get('emotion'):
            request['instructions'] = params['emotion']
        data, content_type = self._request_binary('audio', '/audio/speech', payload=request)
        if cancel.is_set():
            raise GenerationError('任务已取消', 409)
        return [self._save(data, 'audio', content_type)]

    def _video(self, job_id, payload, cancel):
        params, inputs = payload['params'], payload['inputs']
        provider = self.providers['video']
        prompt = '\n'.join(inputs['prompt'])
        if provider['protocol'] == 'seedance':
            content = [{'type': 'text', 'text': prompt}]
            for key, role in (('first', 'first_frame'), ('last', 'last_frame')):
                for reference in inputs.get(key, []):
                    data, mime, _ = self._image_data(reference)
                    content.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,' + base64.b64encode(data).decode()}, 'role': role})
            request = {'model': params['model'], 'content': content, 'duration': params['durationSeconds'],
                       'ratio': params['ratio'], 'resolution': params.get('resolution', '720p')}
            path = '/contents/generations/tasks'
        else:
            size = '720x1280' if params['ratio'] in ('9:16', '3:4') else '1280x720'
            request = {'model': params['model'], 'prompt': prompt, 'seconds': str(int(params['durationSeconds'])), 'size': size}
            if inputs.get('first'):
                data, mime, _ = self._image_data(inputs['first'][0])
                width, height = map(int, size.split('x'))
                with Image.open(io.BytesIO(data)) as image:
                    prepared = ImageOps.pad(ImageOps.exif_transpose(image).convert('RGB'), (width, height), color=(0, 0, 0))
                    buffer = io.BytesIO()
                    prepared.save(buffer, format='PNG')
                request['input_reference'] = {'image_url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()}
            path = '/videos'
        result = self._request_json('video', path, payload=request)
        provider_id = result.get('id')
        if not isinstance(provider_id, str) or not re.fullmatch(r'[\w.-]{1,200}', provider_id):
            raise GenerationError('供应商没有返回有效视频任务 ID', 502)
        with self._connection() as connection:
            connection.execute('UPDATE generation_jobs SET provider_id=?,updated_at=? WHERE id=?', (provider_id, _now(), job_id))
        interval = number(self.settings.get('VIDEO_POLL_INTERVAL') or 3, 'VIDEO_POLL_INTERVAL', .01, 60)
        started = time.monotonic()
        while True:
            if cancel.is_set():
                raise GenerationError('任务已取消', 409)
            status = result.get('status')
            if status in ('succeeded', 'completed'):
                url = result.get('content', {}).get('video_url') if isinstance(result.get('content'), dict) else result.get('url')
                if url:
                    return [self.downloader.download(url, 'video', cancel)]
                if provider['protocol'] == 'openai':
                    data, mime = self._request_binary('video', path + '/' + quote(provider_id, safe='') + '/content', method='GET')
                    return [self._save(data, 'video', mime)]
                raise GenerationError('供应商视频任务缺少下载地址', 502)
            if status in ('failed', 'canceled', 'cancelled', 'expired'):
                raise GenerationError('供应商视频任务失败或已过期，请检查供应商控制台', 502)
            if time.monotonic() - started > 1800:
                raise GenerationError('视频等待超过 30 分钟，请检查供应商任务后再决定是否重试', 504)
            if cancel.wait(interval):
                raise GenerationError('任务已取消', 409)
            result = self._request_json('video', path + '/' + quote(provider_id, safe=''), method='GET')

    def _completion(self, system, user, structured=False):
        provider = self.require_provider('text')
        if not self._text_slots.acquire(blocking=False):
            raise GenerationError('文字规划任务过多，请稍后再试', 429)
        try:
            payload = {'model': provider['model'], 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
            if structured:
                payload['response_format'] = {'type': 'json_object'}
            result = self._request_json('text', '/chat/completions', payload=payload)
            try:
                text = _text(result['choices'][0]['message']['content'], '供应商文字响应', 250000)
                if structured:
                    value = json.loads(text)
                    if not isinstance(value, dict):
                        raise ValueError()
                    return value
                return text
            except (KeyError, IndexError, TypeError, ValueError, GenerationError):
                raise GenerationError('供应商文字结果不是有效的结构化响应', 502) from None
        finally:
            self._text_slots.release()

    def prompt_assist(self, instruction, production=None):
        self.require_provider('text')
        instruction = _text(instruction, 'instruction', 20000)
        context = normalize_production(production) if production is not None else None
        return self._completion('你是影像制作提示词编辑。返回可直接使用的中文提示词，写清主体、外观一致性、场景、光线、构图与运动。只返回提示词文本。',
                                _json({'instruction': instruction, 'production': context}))

    def plan_storyboard(self, production):
        self.require_provider('text')
        try:
            production = normalize_production(production)
        except StudioValidationError as exc:
            raise GenerationError(str(exc)) from None
        schema = {'kind': 'product|comic', 'brief': 'string', 'style': 'string', 'aspect_ratio': '16:9|9:16|1:1', 'duration_seconds': 15,
                  'script': 'string', 'characters': [{'id': 'c1', 'name': 'string', 'description': 'string', 'reference_asset_ids': [], 'voice_id': ''}],
                  'scenes': [{'id': 'scene1', 'name': 'string', 'description': 'string', 'reference_asset_ids': []}],
                  'episodes': [{'id': 'ep1', 'title': 'string', 'synopsis': 'string', 'script': 'string'}],
                  'shots': [{'id': 's1', 'episode_id': 'ep1', 'title': 'string', 'description': 'string', 'image_prompt': 'string', 'video_prompt': 'string', 'dialogue': 'string',
                             'character_ids': ['c1'], 'scene_id': 'scene1', 'duration_seconds': 5, 'order': 0}],
                  'timeline': [{'shot_id': 's1', 'order': 0, 'trim_start': 0, 'trim_end': 5, 'subtitle': 'string'}]}
        result = self._completion('你是专业商品宣传片与分集漫剧导演。返回完整 production JSON 对象，至少规划三个镜头；保留提供的角色/场景/分集 ID 和 reference_asset_ids。不能编造素材 ID。每个镜头需要图像提示词、视频动作、对白、时长和有效引用。故事连贯，商品卖点具体。遵守 schema：' + _json(schema), _json(production), True)
        try:
            result = normalize_production(result.get('production', result), base=production)
            if len(result['shots']) < 3 or any(not shot['image_prompt'].strip() or not shot['video_prompt'].strip() for shot in result['shots']):
                raise StudioValidationError('分镜必须包含至少三个有效镜头及图片、视频提示词')
            allowed_refs = {ref for collection in ('characters', 'scenes') for entry in production[collection] for ref in entry['reference_asset_ids']}
            supplied_refs = {ref for collection in ('characters', 'scenes') for entry in result[collection] for ref in entry['reference_asset_ids']}
            if not supplied_refs.issubset(allowed_refs):
                raise StudioValidationError('AI 结果引用了未提供的素材')
            result['planning_source'] = 'ai'
            return result
        except (StudioValidationError, TypeError, AttributeError):
            raise GenerationError('AI 分镜结构或素材引用无效，请调整需求后重试', 502) from None

    def agent_plan(self, brief, production=None):
        self.require_provider('text')
        brief = _text(brief, 'brief', 20000)
        context = normalize_production(production) if production is not None else None
        system = ('你是画布工作流规划器。返回 JSON {title,nodes,edges}。nodes 形如 {id,type,title,text?,params?,x,y}，'
                  '只允许 prompt,image-gen,image-view,video-gen,video-view,audio-gen,audio-view,compose。'
                  'edges 形如 {source,target,targetHandle,data:{order,mediaType}}。所有 ID 必须存在，不可循环，必须匹配端口。'
                  'prompt 输出 text；image-gen 的 in:prompt 接 text，in:ref 接 image；image-view 的 in:media 接 image；'
                  'video-gen 的 in:prompt 接 text，in:first/in:last 接 image；video-view 的 in:media 接 video；'
                  'audio-gen 的 in:text 接 text，audio-view 的 in:media 接 audio；compose 的 in:video 接 video，in:audio 接 audio。')
        result = self._completion(system, _json({'brief': brief, 'production': context}), True)
        try:
            title = _text(result.get('title'), 'plan.title', 200)
            nodes, edges = result.get('nodes'), result.get('edges')
            if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100 or not isinstance(edges, list) or len(edges) > 200:
                raise ValueError()
            valid_nodes = []
            for node in nodes:
                if node.get('type') in ('asset', 'group'):
                    raise ValueError()
                x, y = number(node.get('x', 0), 'x', -100000, 100000), number(node.get('y', 0), 'y', -100000, 100000)
                if not isinstance(node.get('params', {}), dict):
                    raise ValueError()
                if 'text' in node:
                    _text(node['text'], 'node.text', 32000, True)
                valid_nodes.append({'id': node['id'], 'type': node['type'], 'position': {'x': x, 'y': y}, 'data': {}})
            valid_edges = [{'id': f'edge-{index}', 'sourceHandle': 'out', **edge} for index, edge in enumerate(edges)]
            StudioService.validate_graph(valid_nodes, valid_edges)
            return {'title': title, 'nodes': nodes, 'edges': edges}
        except (KeyError, TypeError, AttributeError, ValueError, StudioValidationError, MediaError):
            raise GenerationError('AI 返回的节点或连线无效，请调整需求后重试', 502) from None
