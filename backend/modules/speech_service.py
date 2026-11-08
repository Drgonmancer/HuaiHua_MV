"""Optional local speech-to-text for automatic subtitles (faster-whisper).

The model is loaded lazily and cached per name; the optional dependency is
never imported at startup so tests and unconfigured installs keep working.
"""
from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

from modules.media_pipeline import MediaError


class SpeechService:
    """Transcribe a local audio file into timestamped subtitle cues."""

    MAX_CUES = 2000

    def __init__(self, model_dir, settings=None):
        self.model_dir = Path(model_dir)
        self.settings = dict(settings or {})
        self._model = None
        self._model_name = ''
        self._lock = threading.Lock()

    @property
    def model_name(self):
        name = str(self.settings.get('SUBTITLE_WHISPER_MODEL') or 'small').strip()
        return name or 'small'

    @property
    def available(self):
        return importlib.util.find_spec('faster_whisper') is not None

    def status(self):
        return {'available': self.available, 'model': self.model_name}

    def _load(self):
        with self._lock:
            if self._model is not None and self._model_name == self.model_name:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise MediaError(
                    '自动字幕需要本机安装 faster-whisper，请执行 '
                    '.venv\\Scripts\\python.exe -m pip install faster-whisper 后重启应用', 400) from exc
            device = str(self.settings.get('SUBTITLE_WHISPER_DEVICE') or 'cpu').strip() or 'cpu'
            compute = str(self.settings.get('SUBTITLE_WHISPER_COMPUTE') or 'int8').strip() or 'int8'
            self.model_dir.mkdir(parents=True, exist_ok=True)
            self._model = WhisperModel(self.model_name, device=device, compute_type=compute,
                                       download_root=str(self.model_dir))
            self._model_name = self.model_name
            return self._model

    def transcribe(self, audio_path, language='', cancel=None):
        """Return cues [{start, end, text}] in seconds for a local audio file."""
        model = self._load()
        try:
            segments, _info = model.transcribe(str(audio_path), language=language or None, beam_size=1)
        except (OSError, ValueError, RuntimeError) as exc:
            raise MediaError('自动字幕转写失败：音频可能无法解码', 422) from exc
        cues = []
        for segment in segments:
            if cancel is not None and cancel.is_set():
                raise MediaError('任务已取消', 409)
            text = str(segment.text or '').strip()
            if not text:
                continue
            start = max(0.0, float(segment.start))
            end = max(start, float(segment.end))
            cues.append({'start': round(start, 3), 'end': round(end, 3), 'text': text})
            if len(cues) >= self.MAX_CUES:
                break
        return cues
