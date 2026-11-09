"""Optional local music generation (ACE-Step) for background music.

ACE-Step is an optional user install from its repository
(pip install -e ./ACE-Step); the 3.5B weights download on first real
generation into data/models/ace-step. Tests inject a fake generator.
"""
from __future__ import annotations

import importlib.util
import shutil
import threading
import uuid
from pathlib import Path

from modules.media_pipeline import MediaError

AUDIO_SUFFIXES = {'.wav', '.mp3', '.flac', '.ogg', '.m4a'}

RESULT_KEYS = ('audio_paths', 'audio_files', 'output_paths', 'files', 'audio_urls')


class MusicService:
    """Generate instrumental/vocal background music locally with ACE-Step."""

    def __init__(self, media_dir, checkpoint_dir, settings=None):
        self.media_dir = Path(media_dir).resolve()
        self.checkpoint_dir = Path(checkpoint_dir)
        self.settings = dict(settings or {})
        self._pipeline = None
        self._lock = threading.Lock()

    @property
    def available(self):
        return importlib.util.find_spec('acestep') is not None

    def status(self):
        return {'available': self.available, 'model': 'ace-step'}

    def _load(self):
        with self._lock:
            if self._pipeline is not None:
                return self._pipeline
            try:
                from acestep.pipeline import AceStepPipeline
            except ImportError as exc:
                raise MediaError(
                    'BGM 生成需要本机安装 ACE-Step：git clone https://github.com/ace-step/ACE-Step '
                    '后 pip install -e ./ACE-Step（装入当前 .venv），安装后重启应用', 400) from exc
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            self._pipeline = AceStepPipeline(str(self.checkpoint_dir))
            return self._pipeline

    @staticmethod
    def _collect_outputs(result, workdir: Path, started_with: set):
        candidates = []
        if isinstance(result, dict):
            for key in RESULT_KEYS:
                value = result.get(key)
                if isinstance(value, list):
                    candidates.extend(value)
                elif isinstance(value, str):
                    candidates.append(value)
        if not candidates:
            # Fallback: the newest audio file created under the workdir.
            produced = [item for item in workdir.rglob('*')
                        if item.is_file() and item.suffix.lower() in AUDIO_SUFFIXES
                        and item.resolve() not in started_with]
            candidates = sorted(produced, key=lambda item: item.stat().st_mtime, reverse=True)
        for candidate in candidates:
            path = Path(candidate)
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES and path.stat().st_size > 0:
                return path
        return None

    def generate(self, tags, lyrics='', duration=30, instrumental=False, cancel=None):
        """Generate one BGM track into media_dir; returns an audio output dict."""
        pipeline = self._load()
        if cancel is not None and cancel.is_set():
            raise MediaError('任务已取消', 409)
        workdir = self.media_dir / ('bgm-' + uuid.uuid4().hex)
        workdir.mkdir(parents=True, exist_ok=True)
        started_with = {item.resolve() for item in self.media_dir.rglob('*') if item.is_file()}
        try:
            prompt_lyrics = '[instrumental]' if instrumental or not lyrics.strip() else lyrics
            result = pipeline(tags=tags, lyrics=prompt_lyrics, duration=float(duration),
                              output_dir=str(workdir))
            produced = self._collect_outputs(result, workdir, started_with)
        except MediaError:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise MediaError('BGM 生成失败，请检查 ACE-Step 安装与显存后重试', 422) from exc
        if produced is None:
            shutil.rmtree(workdir, ignore_errors=True)
            raise MediaError('BGM 生成没有产出音频文件，请检查 ACE-Step 版本', 502)
        filename = uuid.uuid4().hex + produced.suffix.lower()
        target = self.media_dir / filename
        try:
            shutil.move(str(produced), target)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return {'kind': 'audio', 'url': '/studio-media/' + filename,
                'meta': {'tags': tags, 'duration_seconds': duration, 'instrumental': instrumental}}
