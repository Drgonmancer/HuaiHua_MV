"""Bounded local FFmpeg operations. URLs never become FFmpeg network inputs."""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit


class MediaError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def number(value, name, minimum=0, maximum=3600):
    if isinstance(value, bool):
        raise MediaError(f'{name} 必须是数字')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise MediaError(f'{name} 必须是数字') from None
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise MediaError(f'{name} 超出允许范围')
    return result


class MediaPipeline:
    def __init__(self, media_dir, ffmpeg=None, ffprobe=None):
        self.media_dir = Path(media_dir).resolve()
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = ffmpeg or shutil.which('ffmpeg')
        self.ffprobe = ffprobe or shutil.which('ffprobe')

    @property
    def available(self):
        return bool(self.ffmpeg and self.ffprobe)

    def resolve_media(self, url):
        if not isinstance(url, str) or len(url) > 1024:
            raise MediaError('素材 URL 无效')
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise MediaError('导出仅接受本地素材 URL')
        if path.startswith('/studio-media/'):
            root = self.media_dir
            name = path[len('/studio-media/'):]
        elif path.startswith('/canvas-assets/'):
            root = self.media_dir.parent / 'uploads'
            name = path[len('/canvas-assets/'):]
        else:
            raise MediaError('素材必须来自本地素材库')
        if not name or name in ('.', '..') or any(char in name for char in '/\\\x00:'):
            raise MediaError('素材路径无效')
        target = (root / name).resolve()
        if target.parent != root.resolve() or not target.is_file():
            raise MediaError('引用素材不存在或已删除')
        if target.suffix.lower() not in {'.mp4', '.mov', '.webm', '.mkv', '.wav', '.mp3', '.m4a', '.aac', '.ogg', '.flac', '.png', '.jpg', '.jpeg', '.webp'}:
            raise MediaError('素材格式不支持')
        return target

    def _run(self, command, cancel=None, cwd=None, timeout=900):
        if cancel and cancel.is_set():
            raise MediaError('任务已取消', 409)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        # stderr goes to a temporary file to avoid pipe deadlock on long exports.
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors,
                                       cwd=cwd, shell=False, creationflags=flags)
            started = time.monotonic()
            while process.poll() is None:
                if (cancel and cancel.is_set()) or time.monotonic() - started > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise MediaError('任务已取消' if cancel and cancel.is_set() else '媒体处理超时', 409 if cancel and cancel.is_set() else 504)
                time.sleep(.05)
            if process.returncode:
                raise MediaError('媒体处理失败，请检查素材是否完整以及 FFmpeg 是否支持该格式', 422)

    def probe(self, path):
        if not self.available:
            raise MediaError('未找到 FFmpeg 和 ffprobe，请安装后重启', 503)
        try:
            result = subprocess.run([self.ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                     '-show_streams', '-show_format', '-of', 'json', str(path)],
                                    capture_output=True, timeout=30, check=True, shell=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            raise MediaError('素材无法解码，请检查媒体文件', 422) from None

    def validate_export(self, payload):
        if not isinstance(payload, dict):
            raise MediaError('导出参数必须是对象')
        clips = payload.get('clips')
        if not isinstance(clips, list) or not 1 <= len(clips) <= 100:
            raise MediaError('请选择 1 到 100 段视频')
        resolution = payload.get('resolution') or '720p'
        aspect = payload.get('aspect_ratio') or '16:9'
        if aspect not in ('16:9', '9:16', '1:1', '4:3', '3:4', '21:9'):
            raise MediaError('画幅不支持')
        if resolution in ('480p', '720p', '1080p'):
            short = {'480p': 480, '720p': 720, '1080p': 1080}[resolution]
            a, b = map(int, aspect.split(':'))
            width, height = (round(short*a/b/2)*2, short) if a >= b else (short, round(short*b/a/2)*2)
        elif isinstance(resolution, str) and re.fullmatch(r'\d{2,4}x\d{2,4}', resolution):
            width, height = map(int, resolution.split('x'))
        else:
            raise MediaError('分辨率不支持')
        if not 64 <= width <= 3840 or not 64 <= height <= 3840 or width % 2 or height % 2:
            raise MediaError('分辨率必须是 64 到 3840 的偶数')
        fps = number(payload.get('fps', 24), '帧率', 1, 60)
        validated = []
        for clip in clips:
            if not isinstance(clip, dict):
                raise MediaError('镜头参数无效')
            path = self.resolve_media(clip.get('url'))
            start = number(clip.get('trim_start', 0), '剪辑起点')
            end = clip.get('trim_end')
            end = number(end, '剪辑终点') if end is not None and end != 0 else None
            if end is not None and end <= start:
                raise MediaError('剪辑终点必须大于起点')
            subtitle = clip.get('subtitle', '')
            if not isinstance(subtitle, str) or len(subtitle) > 2000:
                raise MediaError('字幕必须是 2000 字以内文本')
            validated.append({'path': path, 'start': start, 'end': end, 'subtitle': subtitle})
        audio = payload.get('audio_urls') or []
        if not isinstance(audio, list) or len(audio) > 8:
            raise MediaError('最多混合 8 段音频')
        audio_paths = [self.resolve_media(url) for url in audio]
        return validated, audio_paths, width, height, fps

    @staticmethod
    def _timestamp(seconds):
        millis = int(round(seconds * 1000))
        return f'{millis//3600000:02}:{millis//60000%60:02}:{millis//1000%60:02},{millis%1000:03}'

    def export(self, payload, cancel=None):
        if not self.available:
            raise MediaError('未找到 FFmpeg 和 ffprobe，请安装后重启', 503)
        clips, audio_paths, width, height, fps = self.validate_export(payload)
        filename = uuid.uuid4().hex + '.mp4'
        output = self.media_dir / filename
        try:
            with tempfile.TemporaryDirectory(prefix='export-', dir=self.media_dir) as temp:
                workspace = Path(temp)
                durations, subtitles = [], []
                total = 0.0
                for index, clip in enumerate(clips):
                    info = self.probe(clip['path'])
                    streams = info.get('streams', [])
                    if not any(stream['codec_type'] == 'video' for stream in streams):
                        raise MediaError('镜头素材没有视频轨道', 422)
                    duration = number(info.get('format', {}).get('duration'), '镜头时长', .01, 3600)
                    end = min(clip['end'] or duration, duration)
                    length = end - clip['start']
                    if length <= .04 or total + length > 3600:
                        raise MediaError('剪辑范围超出素材，或成片超过 60 分钟')
                    durations.append(length)
                    if clip['subtitle'].strip():
                        # Strip cue delimiters and control characters from untrusted subtitle text.
                        text = ''.join(c for c in clip['subtitle'] if c >= ' ' or c == '\n').replace('-->', '→').strip()
                        subtitles.append(f'{len(subtitles)+1}\n{self._timestamp(total)} --> {self._timestamp(total+length)}\n{text}\n')
                    total += length
                    command = [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                               '-ss', str(clip['start']), '-i', str(clip['path'])]
                    has_audio = any(stream['codec_type'] == 'audio' for stream in streams)
                    if not has_audio:
                        command += ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000']
                    command += ['-t', str(length), '-map', '0:v:0', '-map', '0:a:0' if has_audio else '1:a:0',
                                '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1',
                                '-r', str(fps), '-c:v', 'libx264', '-preset', 'fast', '-crf', '22', '-pix_fmt', 'yuv420p',
                                '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-threads', '2', str(workspace / f'clip{index}.mp4')]
                    self._run(command, cancel, workspace)
                (workspace / 'concat.txt').write_text('\n'.join(f"file 'clip{index}.mp4'" for index in range(len(clips))), encoding='utf-8')
                joined = workspace / 'joined.mp4'
                self._run([self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                           '-f', 'concat', '-safe', '1', '-i', 'concat.txt', '-c', 'copy', str(joined)], cancel, workspace)
                command = [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-i', str(joined)]
                for audio in audio_paths:
                    if not any(stream['codec_type'] == 'audio' for stream in self.probe(audio).get('streams', [])):
                        raise MediaError('混音素材没有音频轨道', 422)
                    command += ['-protocol_whitelist', 'file,pipe', '-i', str(audio)]
                if subtitles:
                    (workspace / 'subtitles.srt').write_text('\n'.join(subtitles), encoding='utf-8')
                    command += ['-i', 'subtitles.srt']
                command += ['-map', '0:v:0']
                if audio_paths:
                    inputs = ''.join(f'[{index}:a:0]' for index in range(len(audio_paths)+1))
                    command += ['-filter_complex', f'{inputs}amix=inputs={len(audio_paths)+1}:duration=first:dropout_transition=0:normalize=1[mix]', '-map', '[mix]']
                else:
                    command += ['-map', '0:a:0']
                if subtitles:
                    command += ['-map', f'{len(audio_paths)+1}:s:0', '-c:s', 'mov_text', '-metadata:s:s:0', 'language=zho',
                                '-vf', "subtitles=subtitles.srt:force_style='FontName=Microsoft YaHei,FontSize=20,Outline=1'"]
                command += ['-t', str(total), '-c:v', 'libx264' if subtitles else 'copy', '-c:a', 'aac',
                            '-movflags', '+faststart', '-threads', '2', str(output)]
                self._run(command, cancel, workspace)
            return {'kind': 'video', 'url': '/studio-media/' + filename,
                    'meta': {'duration_seconds': total, 'width': width, 'height': height, 'fps': fps,
                             'clips': len(clips), 'subtitles': bool(subtitles)}}
        except (OSError, MediaError):
            output.unlink(missing_ok=True)
            raise
