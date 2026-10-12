"""Local production metadata, media library and editable storyboard graphs.

This module makes no provider requests. AI planning belongs to GenerationService.
"""
from __future__ import annotations

import copy
import io
import json
import math
import re
import shutil
import sqlite3
import struct
import subprocess
import uuid
import wave
import zlib
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path


class StudioValidationError(ValueError):
    """A bounded request failed validation."""


class StudioUploadTooLarge(StudioValidationError):
    pass


class StudioAssetNotFound(StudioValidationError):
    pass


RATIOS = {'16:9', '9:16', '1:1', '4:3', '3:4', '21:9'}
CATEGORIES = {'product', 'character', 'scene', 'reference', 'video', 'audio'}
LIMITS = {'characters': 50, 'scenes': 100, 'episodes': 100, 'shots': 200, 'timeline': 200}
NODE_ID_KEYS = {'prompt', 'image', 'image_view', 'video', 'video_view', 'audio', 'audio_view', 'compose'}
# Keep these synchronized with frontend/src/nodes/portRules.ts.
PORT_RULES = {
    'prompt': ('text', {'in:context': (('text',), 3)}),
    'asset': ('dynamic', {}),
    'image-gen': ('image', {'in:prompt': (('text',), 4), 'in:ref': (('image',), 6)}),
    'image-view': ('image', {'in:media': (('image',), 1)}),
    'video-gen': ('video', {'in:prompt': (('text',), 2), 'in:first': (('image',), 1), 'in:last': (('image',), 1)}),
    'video-view': ('video', {'in:media': (('video',), 1)}),
    'audio-gen': ('audio', {'in:text': (('text',), 1)}),
    'audio-view': ('audio', {'in:media': (('audio',), 1)}),
    'compose': ('video', {'in:video': (('video',), 1), 'in:audio': (('audio',), 2)}),
    'group': ('dynamic', {}),
}


def _text(value, field, *, maximum=20000, default=''):
    if value is None:
        value = default
    if not isinstance(value, str):
        raise StudioValidationError(f'{field} 必须是字符串')
    if len(value) > maximum or '\x00' in value:
        raise StudioValidationError(f'{field} 超过长度限制或包含无效字符')
    return value


def _id(value, field, *, allow_empty=False):
    value = _text(value, field, maximum=128)
    if not value and allow_empty:
        return value
    if not value or not re.fullmatch(r'[\w.:-]+', value, flags=re.UNICODE):
        raise StudioValidationError(f'{field} ID 格式无效')
    return value


def _number(value, field, *, minimum=0, maximum=3600, integer=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise StudioValidationError(f'{field} 必须是有限数字')
    if value < minimum or value > maximum or (integer and int(value) != value):
        raise StudioValidationError(f'{field} 不在有效范围内')
    return int(value) if integer else value


def _strings(value, field, *, maximum=50):
    if not isinstance(value, list) or len(value) > maximum:
        raise StudioValidationError(f'{field} 必须是数组且数量不能超过 {maximum}')
    ids = [_id(item, field) for item in value]
    if len(ids) != len(set(ids)):
        raise StudioValidationError(f'{field} 包含重复 ID')
    return ids


def normalize_production(value, *, base=None):
    """Merge partial production updates and validate its full stored schema."""
    if not isinstance(value, dict):
        raise StudioValidationError('production 必须是对象')
    data = {**copy.deepcopy(base or {}), **copy.deepcopy(value)}
    kind = data.get('kind', 'product')
    if kind not in ('product', 'comic'):
        raise StudioValidationError('kind 必须是 product 或 comic')
    ratio = data.get('aspect_ratio', '16:9' if kind == 'product' else '9:16')
    if not isinstance(ratio, str) or ratio not in RATIOS:
        raise StudioValidationError('aspect_ratio 格式无效')
    result = {
        'kind': kind,
        'brief': _text(data.get('brief', ''), 'brief'),
        'style': _text(data.get('style', '写实商业摄影' if kind == 'product' else '电影感国漫'), 'style', maximum=1000),
        'aspect_ratio': ratio,
        'duration_seconds': _number(data.get('duration_seconds', 15), 'duration_seconds', minimum=1),
        'script': _text(data.get('script', ''), 'script', maximum=100000),
    }
    for field, limit in LIMITS.items():
        items = data.get(field, [])
        if not isinstance(items, list) or len(items) > limit:
            raise StudioValidationError(f'{field} 必须是数组且数量不能超过 {limit}')
        if any(not isinstance(item, dict) for item in items):
            raise StudioValidationError(f'{field} 每一项必须是对象')
        result[field] = []
        seen = set()
        for index, item in enumerate(items):
            label = f'{field}[{index}]'
            if field == 'timeline':
                entry = {
                    'shot_id': _id(item.get('shot_id'), label + '.shot_id'),
                    'order': _number(item.get('order', index), label + '.order', integer=True, maximum=10000),
                    'trim_start': _number(item.get('trim_start', 0), label + '.trim_start'),
                    'trim_end': _number(item.get('trim_end', 0), label + '.trim_end'),
                    'subtitle': _text(item.get('subtitle', ''), label + '.subtitle', maximum=10000),
                }
                if entry['trim_end'] and entry['trim_end'] <= entry['trim_start']:
                    raise StudioValidationError(f'{label} trim_end 必须大于 trim_start')
                unique_id = entry['shot_id']
            else:
                unique_id = _id(item.get('id'), label + '.id')
                entry = {'id': unique_id}
                if field in ('characters', 'scenes'):
                    entry.update(name=_text(item.get('name', ''), label + '.name', maximum=200), description=_text(item.get('description', ''), label + '.description'), reference_asset_ids=_strings(item.get('reference_asset_ids', []), label + '.reference_asset_ids', maximum=6))
                    if field == 'characters':
                        entry['voice_id'] = _text(item.get('voice_id', ''), label + '.voice_id', maximum=200)
                elif field == 'episodes':
                    entry.update(title=_text(item.get('title', ''), label + '.title', maximum=200), synopsis=_text(item.get('synopsis', ''), label + '.synopsis'), script=_text(item.get('script', ''), label + '.script', maximum=100000))
                elif field == 'shots':
                    entry.update(
                        episode_id=_id(item.get('episode_id', ''), label + '.episode_id', allow_empty=True),
                        title=_text(item.get('title', ''), label + '.title', maximum=200),
                        description=_text(item.get('description', ''), label + '.description'),
                        image_prompt=_text(item.get('image_prompt', ''), label + '.image_prompt'),
                        video_prompt=_text(item.get('video_prompt', ''), label + '.video_prompt'),
                        dialogue=_text(item.get('dialogue', ''), label + '.dialogue', maximum=10000),
                        character_ids=_strings(item.get('character_ids', []), label + '.character_ids'),
                        scene_id=_id(item.get('scene_id', ''), label + '.scene_id', allow_empty=True),
                        duration_seconds=_number(item.get('duration_seconds', 5), label + '.duration_seconds', minimum=0.1, maximum=300),
                        order=_number(item.get('order', index), label + '.order', integer=True, maximum=10000),
                    )
                    if 'node_ids' in item:
                        ids = item['node_ids']
                        if not isinstance(ids, dict) or any(key not in NODE_ID_KEYS for key in ids):
                            raise StudioValidationError(f'{label}.node_ids 格式无效')
                        entry['node_ids'] = {key: _id(identifier, label + '.node_ids.' + key) for key, identifier in ids.items() if identifier}
            if unique_id in seen:
                raise StudioValidationError(f'{field} 包含重复 ID: {unique_id}')
            seen.add(unique_id)
            result[field].append(entry)
    characters = {item['id'] for item in result['characters']}
    scenes = {item['id'] for item in result['scenes']}
    episodes = {item['id'] for item in result['episodes']}
    shots = {item['id']: item for item in result['shots']}
    for shot in result['shots']:
        for character_id in shot['character_ids']:
            if character_id not in characters:
                raise StudioValidationError(f"镜头 {shot['id']} 引用了不存在的角色 {character_id}")
        if shot['scene_id'] and shot['scene_id'] not in scenes:
            raise StudioValidationError(f"镜头 {shot['id']} 引用了不存在的场景 {shot['scene_id']}")
        if shot['episode_id'] and shot['episode_id'] not in episodes:
            raise StudioValidationError(f"镜头 {shot['id']} 引用了不存在的分集 {shot['episode_id']}")
    for entry in result['timeline']:
        shot = shots.get(entry['shot_id'])
        if not shot:
            raise StudioValidationError(f"时间线引用了不存在的镜头 {entry['shot_id']}")
        if entry['trim_start'] >= shot['duration_seconds'] or entry['trim_end'] > shot['duration_seconds']:
            raise StudioValidationError(f"镜头 {shot['id']} 的裁剪超出时长范围")
    if 'planning_source' in data:
        if data['planning_source'] not in ('template', 'ai'):
            raise StudioValidationError('planning_source 格式无效')
        result['planning_source'] = data['planning_source']
    if 'voice_settings' in data:
        voice = data['voice_settings']
        if not isinstance(voice, dict):
            raise StudioValidationError('voice_settings 必须是对象')
        result['voice_settings'] = {
            'voice_id': _text(voice.get('voice_id', ''), 'voice_settings.voice_id', maximum=200),
            'model': _text(voice.get('model', ''), 'voice_settings.model', maximum=200),
            'speed': _number(voice.get('speed', 1), 'voice_settings.speed', minimum=0.25, maximum=4),
            'emotion': _text(voice.get('emotion', ''), 'voice_settings.emotion', maximum=200),
        }
    return result


TEMPLATES = [
    {'id': 'product-commercial', 'kind': 'product', 'name': '商品宣传片', 'description': '本地可编辑模板：商品亮相、卖点演示、购买引导。', 'steps': ['上传商品参考图', '明确卖点和目标人群', '编辑三段分镜与旁白', '生成图片、视频和配音', '按镜头顺序合成成片']},
    {'id': 'comic-drama', 'kind': 'comic', 'name': '分集漫剧', 'description': '本地可编辑模板：建立角色与场景，以开场、冲突和悬念展开分集。', 'steps': ['建立角色、声音和场景参考', '填写分集剧情与剧本', '编辑分镜与对白', '生成分集镜头并保持角色一致', '合成带字幕的剧集']},
]


class StudioService:
    def __init__(self, db_path, media_dir=None):
        self.db_path = str(db_path)
        self.media_dir = Path(media_dir or Path(db_path).parent / 'studio-media').resolve()
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.max_asset_bytes = 100 * 1024 * 1024
        with self._connection() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS studio_assets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
                media_type TEXT NOT NULL, filename TEXT NOT NULL UNIQUE,
                mime_type TEXT NOT NULL, created_at TEXT NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_studio_assets_category ON studio_assets(category, created_at DESC)')

    @contextmanager
    def _connection(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def templates():
        return copy.deepcopy(TEMPLATES)

    @staticmethod
    def _asset(row):
        return {'id': row['id'], 'name': row['name'], 'category': row['category'], 'media_type': row['media_type'], 'url': '/studio-media/' + row['filename'], 'created_at': row['created_at']}

    def list_assets(self, *, category=None, q=''):
        if category and category not in CATEGORIES:
            raise StudioValidationError('素材 category 无效')
        q = _text(q, 'q', maximum=200)
        query, params = 'SELECT * FROM studio_assets WHERE 1=1', []
        if category:
            query += ' AND category = ?'
            params.append(category)
        if q:
            query += " AND name LIKE ? ESCAPE '\\'"
            params.append('%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        query += ' ORDER BY created_at DESC LIMIT 500'
        with self._connection() as conn:
            return [self._asset(row) for row in conn.execute(query, params)]

    def get_asset(self, asset_id):
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM studio_assets WHERE id = ?', (asset_id,)).fetchone()
        if not row or not (self.media_dir / row['filename']).is_file():
            raise StudioAssetNotFound(f'素材 {asset_id} 不存在或文件已删除，请重新上传或移除引用')
        return self._asset(row)

    def validate_references(self, production):
        for role in production['characters'] + production['scenes']:
            for asset_id in role['reference_asset_ids']:
                asset = self.get_asset(asset_id)
                if asset['media_type'] != 'image':
                    raise StudioValidationError(f'素材 {asset_id} 必须是图片才能作为角色、商品或场景参考')

    @staticmethod
    def _png_valid(data):
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):
            return False
        offset, first, has_data = 8, True, False
        try:
            while offset + 12 <= len(data):
                size = struct.unpack('>I', data[offset:offset + 4])[0]
                kind = data[offset + 4:offset + 8]
                end = offset + 12 + size
                if end > len(data) or size > 10 * 1024 * 1024:
                    return False
                body = data[offset + 8:offset + 8 + size]
                checksum = struct.unpack('>I', data[offset + 8 + size:end])[0]
                if zlib.crc32(kind + body) & 0xffffffff != checksum:
                    return False
                if first:
                    if kind != b'IHDR' or size != 13 or zlib.crc32(kind + body) & 0xffffffff != checksum:
                        return False
                    width, height = struct.unpack('>II', body[:8])
                    if not width or not height or width * height > 40_000_000:
                        return False
                    first = False
                has_data = has_data or kind == b'IDAT'
                if kind == b'IEND':
                    return size == 0 and has_data and end == len(data)
                offset = end
        except (struct.error, ValueError):
            return False
        return False

    @classmethod
    def _detect_media(cls, data, extension):
        if extension == '.png' and cls._png_valid(data):
            return 'image', 'image/png'
        if extension in ('.jpg', '.jpeg') and len(data) > 20 and data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):
            return 'image', 'image/jpeg'
        if extension == '.webp' and len(data) > 20 and data.startswith(b'RIFF') and data[8:12] == b'WEBP' and struct.unpack('<I', data[4:8])[0] + 8 == len(data):
            return 'image', 'image/webp'
        if extension == '.wav':
            try:
                with wave.open(io.BytesIO(data), 'rb') as audio:
                    if audio.getnchannels() in (1, 2) and 8000 <= audio.getframerate() <= 192000 and 0 < audio.getnframes() <= audio.getframerate() * 3600:
                        audio.readframes(audio.getnframes())
                        return 'audio', 'audio/wav'
            except (wave.Error, EOFError):
                pass
        if extension == '.mp3' and len(data) > 128 and (data.startswith(b'ID3') or data[0] == 0xff and data[1] & 0xe0 == 0xe0):
            return 'audio', 'audio/mpeg'
        if extension in ('.mp4', '.m4a', '.mov') and len(data) > 32 and data[4:8] == b'ftyp':
            return ('audio', 'audio/mp4') if extension == '.m4a' else ('video', 'video/quicktime' if extension == '.mov' else 'video/mp4')
        if extension in ('.webm', '.ogg') and len(data) > 32:
            if extension == '.webm' and data.startswith(b'\x1aE\xdf\xa3'):
                return 'video', 'video/webm'
            if extension == '.ogg' and data.startswith(b'OggS'):
                return 'audio', 'audio/ogg'
        raise StudioValidationError('素材文件格式无效；支持 PNG/JPEG/WebP、WAV/MP3/M4A/OGG 和 MP4/MOV/WebM')

    def add_asset(self, upload, *, name='', category='reference'):
        if category not in CATEGORIES:
            raise StudioValidationError('素材 category 无效')
        if not upload or not upload.filename:
            raise StudioValidationError('必须选择素材文件')
        extension = Path(upload.filename).suffix.lower()
        size_limit = min(self.max_asset_bytes, 10 * 1024 * 1024 if extension in ('.png', '.jpg', '.jpeg', '.webp') else 100 * 1024 * 1024)
        data = upload.stream.read(size_limit + 1)
        if len(data) > size_limit:
            raise StudioUploadTooLarge(f'素材超过大小限制（最多 {size_limit // (1024 * 1024)} MB）')
        media_type, mime = self._detect_media(data, extension)
        declared = upload.mimetype
        aliases = {'image/jpg': 'image/jpeg', 'audio/x-wav': 'audio/wav', 'audio/wave': 'audio/wav', 'video/x-m4v': 'video/mp4'}
        if declared and declared != 'application/octet-stream' and aliases.get(declared, declared) != mime:
            raise StudioValidationError('素材 MIME 类型与文件格式不匹配')
        if category in ('product', 'character', 'scene', 'reference') and media_type != 'image':
            raise StudioValidationError('商品、角色、场景与参考分类必须上传图片素材')
        if category in ('video', 'audio') and category != media_type:
            raise StudioValidationError(f'{category} 分类的素材媒体类型无效')
        name = _text(name or Path(upload.filename.replace('\\', '/')).name, '素材 name', maximum=200).strip()
        if not name:
            raise StudioValidationError('素材 name 必须是非空字符串')
        asset_id = uuid.uuid4().hex
        filename = asset_id + ('.jpg' if extension == '.jpeg' else extension)
        path = self.media_dir / filename
        try:
            path.write_bytes(data)
            if media_type == 'image':
                # Pillow verifies the file structure and then decodes pixels;
                # magic bytes alone cannot detect truncated JPEG/WebP content.
                try:
                    from PIL import Image, UnidentifiedImageError
                except ImportError as exc:
                    raise StudioValidationError('图片素材验证需要 Pillow，请安装项目依赖') from exc
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        if image.width * image.height > 40_000_000 or image.width < 1 or image.height < 1:
                            raise StudioValidationError('图片尺寸超出有效范围')
                        image.verify()
                    with Image.open(io.BytesIO(data)) as image:
                        image.load()
                except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
                    raise StudioValidationError('图片素材无法解码，文件内容无效') from exc
            if media_type in ('video', 'audio'):
                probe = shutil.which('ffprobe')
                if not probe:
                    raise StudioValidationError('音频与视频素材验证需要本地 FFprobe，请先安装 FFmpeg')
                try:
                    result = subprocess.run([probe, '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', str(path)], capture_output=True, timeout=20, check=False)
                    metadata = json.loads(result.stdout or b'{}')
                    if result.returncode or not any(stream.get('codec_type') == media_type for stream in metadata.get('streams', [])):
                        raise StudioValidationError('素材媒体内容无效或缺少可解码的音视频轨道')
                except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                    raise StudioValidationError('素材媒体内容验证失败') from exc
            with self._connection() as conn:
                conn.execute('INSERT INTO studio_assets (id,name,category,media_type,filename,mime_type,created_at) VALUES (?,?,?,?,?,?,?)', (asset_id, name, category, media_type, filename, mime, datetime.now(timezone.utc).isoformat()))
            return self.get_asset(asset_id)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def delete_asset(self, asset_id):
        with self._connection() as conn:
            row = conn.execute('SELECT filename FROM studio_assets WHERE id = ?', (asset_id,)).fetchone()
            if not row:
                raise StudioAssetNotFound(f'素材 {asset_id} 不存在')
            path = (self.media_dir / row['filename']).resolve()
            if path.parent != self.media_dir:
                raise StudioValidationError('素材文件路径无效')
            path.unlink(missing_ok=True)
            conn.execute('DELETE FROM studio_assets WHERE id = ?', (asset_id,))

    @staticmethod
    def validate_graph(nodes, edges):
        if not isinstance(nodes, list) or len(nodes) > 1000 or any(not isinstance(n, dict) for n in nodes):
            raise StudioValidationError('nodes 必须是对象数组且数量不能超过 1000')
        if not isinstance(edges, list) or len(edges) > 3000 or any(not isinstance(e, dict) for e in edges):
            raise StudioValidationError('edges 必须是对象数组且数量不能超过 3000')
        by_id, edge_ids, counts, pairs = {}, set(), {}, set()
        for node in nodes:
            node_id = _id(node.get('id'), 'node.id')
            if node_id in by_id:
                raise StudioValidationError('nodes 包含重复 ID')
            if not isinstance(node.get('type'), str) or node.get('type') not in PORT_RULES:
                raise StudioValidationError('节点类型无效')
            if not isinstance(node.get('data', {}), dict):
                raise StudioValidationError('node.data 必须是对象')
            position = node.get('position', {})
            if not isinstance(position, dict):
                raise StudioValidationError('node.position 必须是对象')
            for axis in ('x', 'y'):
                _number(position.get(axis, 0), 'node.position.' + axis, minimum=-1000000, maximum=1000000)
            if node['type'] == 'asset' and node.get('data', {}).get('mediaType', 'image') not in ('image', 'video', 'audio'):
                raise StudioValidationError('素材节点 mediaType 无效')
            by_id[node_id] = node
        adjacency, indegree = {key: [] for key in by_id}, {key: 0 for key in by_id}
        for edge in edges:
            edge_id = _id(edge.get('id'), 'edge.id')
            if edge_id in edge_ids:
                raise StudioValidationError('edges 包含重复 ID')
            edge_ids.add(edge_id)
            source_id = _id(edge.get('source'), 'edge.source')
            target_id = _id(edge.get('target'), 'edge.target')
            source, target = by_id.get(source_id), by_id.get(target_id)
            if not source or not target or source['id'] == target['id']:
                raise StudioValidationError('连线节点不存在或不能连接自身')
            if edge.get('sourceHandle') != 'out':
                raise StudioValidationError('连线输出端口无效')
            produced = PORT_RULES[source['type']][0]
            if produced == 'dynamic':
                produced = source.get('data', {}).get('mediaType', 'image') if source['type'] == 'asset' else None
            handle = _text(edge.get('targetHandle'), 'edge.targetHandle', maximum=100)
            port = PORT_RULES[target['type']][1].get(handle)
            if not port or produced not in port[0]:
                raise StudioValidationError('连线输入端口或媒体类型无效')
            pair = (source['id'], target['id'], handle)
            if pair in pairs:
                raise StudioValidationError('存在重复连线')
            pairs.add(pair)
            port_key = (target['id'], handle)
            counts[port_key] = counts.get(port_key, 0) + 1
            if counts[port_key] > port[1]:
                raise StudioValidationError('连线超过输入端口容量')
            data = edge.get('data', {})
            if not isinstance(data, dict) or data.get('mediaType', produced) != produced:
                raise StudioValidationError('edge.data 媒体类型无效')
            _number(data.get('order', 0), 'edge.order', integer=True, maximum=10000)
            adjacency[source['id']].append(target['id'])
            indegree[target['id']] += 1
        ready, visited = [key for key, count in indegree.items() if count == 0], 0
        while ready:
            current = ready.pop()
            visited += 1
            for target in adjacency[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != len(nodes):
            raise StudioValidationError('节点图包含循环连线')

    @staticmethod
    def _stable_id(*parts):
        return 'studio-' + uuid.uuid5(uuid.NAMESPACE_URL, '|'.join(parts)).hex

    def template_production(self, production):
        data = copy.deepcopy(production)
        kind = data['kind']
        if not data['characters']:
            data['characters'] = [{'id': kind + '-primary', 'name': '主推商品' if kind == 'product' else '主角', 'description': '请填写商品外观、核心卖点' if kind == 'product' else '请填写角色外貌、性格与关系', 'reference_asset_ids': [], 'voice_id': ''}]
        if not data['scenes']:
            data['scenes'] = [{'id': kind + '-scene', 'name': '商品展示空间' if kind == 'product' else '故事主场景', 'description': '简洁背景、柔和主光' if kind == 'product' else '请填写故事地点、时间与氛围', 'reference_asset_ids': []}]
        if not data['episodes']:
            data['episodes'] = [{'id': kind + '-episode-1', 'title': '宣传片' if kind == 'product' else '第一集', 'synopsis': data['brief'], 'script': data['script']}]
        if not data['shots']:
            beats = [('商品亮相', '展示商品整体外观，建立视觉焦点', '发现更适合你的选择'), ('卖点演示', '近景展示商品核心卖点与实际使用', '让每个细节更出色'), ('购买引导', '商品定格与使用成果，预留品牌和行动文案空间', '现在开启新的体验')] if kind == 'product' else [('开场建立', '建立角色、场景和当前目标', '故事从这里开始'), ('冲突发展', '角色面对障碍或意外，推进关系与冲突', '事情并没有那么简单'), ('结尾悬念', '揭示新线索或选择，留下下一集悬念', '答案，留在下一次相遇')]
            duration = round(max(0.1, min(300, data['duration_seconds'] / 3 / len(data['episodes']))), 2)
            for episode in data['episodes']:
                for index, (title, description, dialogue) in enumerate(beats):
                    shot_id = self._stable_id(kind, episode['id'], 'shot', str(index))
                    data['shots'].append({'id': shot_id, 'episode_id': episode['id'], 'title': title, 'description': description, 'image_prompt': f"{data['style']}，{description}。{episode['synopsis'] or data['brief']}", 'video_prompt': f'{description}，自然运动与平稳镜头，保持角色和商品外观一致', 'dialogue': dialogue, 'character_ids': [item['id'] for item in data['characters'][:3]], 'scene_id': data['scenes'][0]['id'], 'duration_seconds': duration, 'order': len(data['shots'])})
        return normalize_production(data)

    def build_storyboard(self, production, *, source='template', episode_id=None):
        data = normalize_production(production)
        if source == 'template':
            data = self.template_production(data)
        elif source != 'ai' or not data['shots']:
            raise StudioValidationError('AI 分镜结果必须包含有效 shots；未生成镜头不能标为 AI 规划成功')
        data['planning_source'] = source
        self.validate_references(data)
        characters = {role['id']: role for role in data['characters']}
        scenes = {role['id']: role for role in data['scenes']}
        if episode_id is not None and episode_id not in {ep['id'] for ep in data['episodes']}:
            raise StudioValidationError('分集不存在')
        shots = sorted([shot for shot in data['shots'] if episode_id is None or shot['episode_id'] == episode_id], key=lambda shot: (shot['order'], shot['id']))
        if not shots:
            raise StudioValidationError('所选分集没有分镜')
        timeline = {entry['shot_id']: entry for entry in data['timeline']}
        data['timeline'] = [timeline.get(shot['id'], {'shot_id': shot['id'], 'order': shot['order'], 'trim_start': 0, 'trim_end': shot['duration_seconds'], 'subtitle': shot['dialogue']}) for shot in sorted(data['shots'], key=lambda shot: (shot['order'], shot['id']))]
        nodes, edges, by_id = [], [], {}
        def node(node_id, kind, title, x, y, *, text=None, url=None, params=None, meta=None):
            entry = {'id': node_id, 'type': kind, 'position': {'x': x, 'y': y}, 'data': {'title': title, 'params': params or {}, 'runtime': {'status': 'idle'}, **(meta or {})}}
            if text is not None:
                entry['data']['value'] = {'text': text}
            if url:
                entry['data'].update(mediaType='image', value={'url': url})
            nodes.append(entry)
            by_id[node_id] = entry
            return node_id
        def edge(source_id, target_id, handle, order=0):
            source_node = by_id[source_id]
            media_type = PORT_RULES[source_node['type']][0]
            if media_type == 'dynamic':
                media_type = source_node['data']['mediaType']
            edges.append({'id': self._stable_id('edge', source_id, target_id, handle), 'source': source_id, 'target': target_id, 'sourceHandle': 'out', 'targetHandle': handle, 'data': {'order': order, 'mediaType': media_type}})
        context_id = self._stable_id(data['kind'], 'context')
        node(context_id, 'prompt', '作品设定 · ' + ('商品宣传片' if data['kind'] == 'product' else '分集漫剧'), 0, 0, text=f"{data['brief']}\n风格：{data['style']}\n剧本：{data['script']}")
        reference_nodes = {}
        for index, shot in enumerate(shots):
            y = 250 + index * 650
            ids = dict(shot.get('node_ids', {}))
            for key in NODE_ID_KEYS:
                ids.setdefault(key, self._stable_id(shot['id'], key))
            roles = [characters[role_id] for role_id in shot['character_ids']]
            if shot['scene_id']:
                roles.append(scenes[shot['scene_id']])
            descriptions = '\n'.join(f"{role['name']}：{role['description']}" for role in roles)
            shot_meta = {'shot_id': shot['id'], 'episode_id': shot['episode_id']}
            image_text = shot['image_prompt'] or shot['description'] or shot['title']
            node(ids['prompt'], 'prompt', shot['title'] + ' · 画面提示词', 260, y, text=f"{image_text}\n{descriptions}\n风格：{data['style']}", meta=shot_meta)
            edge(context_id, ids['prompt'], 'in:context')
            node(ids['image'], 'image-gen', shot['title'] + ' · 图片', 580, y, params={'ratio': data['aspect_ratio'], 'count': 1}, meta=shot_meta)
            edge(ids['prompt'], ids['image'], 'in:prompt')
            refs = []
            for role in roles:
                for asset_id in role['reference_asset_ids']:
                    ref_key = (role['id'], asset_id)
                    if ref_key not in reference_nodes:
                        asset = self.get_asset(asset_id)
                        ref_id = self._stable_id('reference', role['id'], asset_id)
                        node(ref_id, 'asset', role['name'] + ' · ' + asset['name'], 0, 200 + len(reference_nodes) * 210, url=asset['url'], meta={'asset_id': asset_id, 'reference_owner_id': role['id']})
                        reference_nodes[ref_key] = ref_id
                    if reference_nodes[ref_key] not in refs:
                        refs.append(reference_nodes[ref_key])
            if len(refs) > 6:
                raise StudioValidationError(f"镜头 {shot['id']} 的商品、角色和场景参考图总数超过 6，请减少参考素材")
            for ref_order, ref_id in enumerate(refs):
                edge(ref_id, ids['image'], 'in:ref', ref_order)
            node(ids['image_view'], 'image-view', shot['title'] + ' · 图片预览', 920, y, meta=shot_meta)
            edge(ids['image'], ids['image_view'], 'in:media')
            video_prompt = self._stable_id(shot['id'], 'video_prompt')
            node(video_prompt, 'prompt', shot['title'] + ' · 运动提示词', 920, y + 250, text=shot['video_prompt'] or shot['description'] or shot['title'], meta=shot_meta)
            edge(context_id, video_prompt, 'in:context')
            node(ids['video'], 'video-gen', shot['title'] + ' · 视频', 1250, y, params={'ratio': data['aspect_ratio'], 'durationSeconds': shot['duration_seconds']}, meta=shot_meta)
            edge(video_prompt, ids['video'], 'in:prompt')
            edge(ids['image_view'], ids['video'], 'in:first')
            node(ids['video_view'], 'video-view', shot['title'] + ' · 视频预览', 1590, y, meta=shot_meta)
            edge(ids['video'], ids['video_view'], 'in:media')
            actual_ids = {key: ids[key] for key in ('prompt', 'image', 'image_view', 'video', 'video_view')}
            if shot['dialogue'].strip():
                speech_id = self._stable_id(shot['id'], 'speech_prompt')
                node(speech_id, 'prompt', shot['title'] + ' · 对白', 260, y + 300, text=shot['dialogue'], meta=shot_meta)
                edge(context_id, speech_id, 'in:context')
                voice = data.get('voice_settings', {})
                voice_id = next((characters[role_id]['voice_id'] for role_id in shot['character_ids'] if characters[role_id]['voice_id']), voice.get('voice_id', ''))
                params = {'mode': 'tts', 'speed': voice.get('speed', 1), 'emotion': voice.get('emotion', '')}
                if voice_id:
                    params['voiceId'] = voice_id
                if voice.get('model'):
                    params['model'] = voice['model']
                node(ids['audio'], 'audio-gen', shot['title'] + ' · 配音', 580, y + 300, params=params, meta=shot_meta)
                edge(speech_id, ids['audio'], 'in:text')
                node(ids['audio_view'], 'audio-view', shot['title'] + ' · 配音预览', 1590, y + 300, meta=shot_meta)
                edge(ids['audio'], ids['audio_view'], 'in:media')
                node(ids['compose'], 'compose', shot['title'] + ' · 音画合成', 1940, y, params={'ratio': data['aspect_ratio']}, meta=shot_meta)
                edge(ids['video_view'], ids['compose'], 'in:video')
                edge(ids['audio_view'], ids['compose'], 'in:audio')
                actual_ids.update(audio=ids['audio'], audio_view=ids['audio_view'], compose=ids['compose'])
            shot['node_ids'] = actual_ids
        self.validate_graph(nodes, edges)
        return {'production': data, 'nodes': nodes, 'edges': edges, 'title': '商品宣传片分镜' if data['kind'] == 'product' else '漫剧分镜', 'source': source}
