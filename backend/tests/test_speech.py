import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class SpeechServiceTests(unittest.TestCase):
    def setUp(self):
        from modules.speech_service import SpeechService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = SpeechService(Path(self.temp.name) / 'models', settings={})

    def test_status_reports_model_name_and_availability(self):
        status = self.service.status()
        self.assertEqual(status['model'], 'small')
        self.assertIsInstance(status['available'], bool)

    def test_model_name_follows_configuration(self):
        from modules.speech_service import SpeechService
        custom = SpeechService(Path(self.temp.name), settings={'SUBTITLE_WHISPER_MODEL': 'large-v3'})
        self.assertEqual(custom.model_name, 'large-v3')

    def test_missing_dependency_raises_actionable_media_error(self):
        from modules.media_pipeline import MediaError
        with patch.dict(sys.modules, {'faster_whisper': None}):
            with self.assertRaises(MediaError) as caught:
                self.service.transcribe(Path(self.temp.name) / 'a.wav')
        self.assertEqual(caught.exception.status, 400)
        self.assertIn('faster-whisper', str(caught.exception))


class SubtitleMergeTests(unittest.TestCase):
    def test_empty_transcript_falls_back_to_manual(self):
        from modules.media_pipeline import MediaPipeline
        manual = [{'start': 0, 'end': 1, 'text': '手动'}]
        cues, source = MediaPipeline._merge_subtitles(manual, [])
        self.assertEqual(source, 'manual')
        self.assertEqual([cue['text'] for cue in cues], ['手动'])

    def test_auto_cues_win_on_overlap_and_keep_nonoverlapping_manual(self):
        from modules.media_pipeline import MediaPipeline
        manual = [{'start': 0, 'end': .8, 'text': '手动'},
                  {'start': .8, 'end': 1.6, 'text': '保留'}]
        auto = [{'start': .1, 'end': .5, 'text': '自动'},
                {'start': 2, 'end': 2.4, 'text': '识别'}]
        cues, source = MediaPipeline._merge_subtitles(manual, auto)
        self.assertEqual(source, 'mixed')
        self.assertEqual([(cue['text'], cue['start']) for cue in cues],
                         [('自动', .1), ('保留', .8), ('识别', 2)])


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg unavailable')
class AutoSubtitleExportTests(unittest.TestCase):
    def setUp(self):
        from modules.media_pipeline import MediaPipeline
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = Path(self.temp.name) / 'studio-media'
        self.media.mkdir()
        self.pipeline = MediaPipeline(self.media)
        for index, color in enumerate(('red', 'blue')):
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                            '-f', 'lavfi', '-i', f'color=c={color}:s=160x90:r=12:d=1.2',
                            '-f', 'lavfi', '-i', f'sine=frequency={440+index*220}:duration=1.2',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest',
                            str(self.media / f'clip{index}.mp4')], check=True, capture_output=True)

    def clips(self):
        return [{'url': '/studio-media/clip0.mp4', 'trim_start': .2, 'trim_end': 1, 'subtitle': '手动字'},
                {'url': '/studio-media/clip1.mp4', 'trim_start': .2, 'trim_end': 1, 'subtitle': ''}]

    def export(self, transcriber):
        self.pipeline.transcriber = transcriber
        return self.pipeline.export({'clips': self.clips(), 'resolution': '320x180', 'fps': 12,
                                     'auto_subtitles': True, 'subtitle_language': 'zh'})

    def extract_subtitles(self, url):
        path = self.media / Path(url).name
        return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:s:0', '-f', 'srt', '-'],
                              check=True, capture_output=True).stdout.decode('utf-8')

    def test_transcribed_cues_replace_overlapped_manual_subtitle(self):
        result = self.export(lambda audio, language, cancel: [
            {'start': .1, 'end': .5, 'text': '自动一'},
            {'start': 1.0, 'end': 1.5, 'text': '自动二'}])
        self.assertEqual(result['meta']['subtitle_source'], 'auto')
        self.assertEqual(result['meta']['subtitle_cues'], 2)
        subtitles = self.extract_subtitles(result['url'])
        self.assertIn('自动一', subtitles)
        self.assertIn('自动二', subtitles)
        self.assertNotIn('手动字', subtitles)

    def test_empty_transcript_falls_back_to_manual_cues(self):
        result = self.export(lambda audio, language, cancel: [])
        self.assertEqual(result['meta']['subtitle_source'], 'manual')
        self.assertIn('手动字', self.extract_subtitles(result['url']))

    def test_missing_transcriber_is_a_clear_media_error(self):
        self.pipeline.transcriber = None
        from modules.media_pipeline import MediaError
        with self.assertRaises(MediaError) as caught:
            self.export(None)
        self.assertEqual(caught.exception.status, 400)

    def test_transcription_failure_cleans_up_partial_output(self):
        def broken(audio, language, cancel):
            raise RuntimeError('model crashed')
        with self.assertRaises(Exception):
            self.export(broken)
        leftovers = [item for item in self.media.iterdir() if item.name.endswith('.mp4') and item.name != 'clip0.mp4' and item.name != 'clip1.mp4']
        self.assertEqual(leftovers, [])


class ExportServiceWiringTests(unittest.TestCase):
    def setUp(self):
        from modules.generation_service import GenerationService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'canvas.db'
        self.service = GenerationService(self.db, settings={})
        self.addCleanup(self.service.close)
        # A placeholder clip file passes URL validation; export itself is faked per test.
        self.service.media_dir.mkdir(exist_ok=True)
        (self.service.media_dir / 'a.mp4').write_bytes(b'clip')

    def clip_payload(self, **extra):
        payload = {'project_id': 'p1', 'clips': [{'url': '/studio-media/a.mp4'}], 'resolution': '720p'}
        payload.update(extra)
        return payload

    def wait_job(self, job_id):
        for _ in range(200):
            job = self.service.get_job(job_id)
            if job['status'] in ('succeeded', 'failed', 'canceled'):
                return job
            time.sleep(.02)
        self.fail('export job did not finish')

    def test_settings_status_exposes_subtitle_capability(self):
        status = self.service.settings_status()
        self.assertIn('subtitles', status)
        self.assertEqual(status['subtitles']['model'], 'small')

    def test_unavailable_transcriber_rejects_auto_export_before_queueing(self):
        from modules.generation_service import GenerationError
        self.service.speech = types.SimpleNamespace(available=False, status=lambda: {'available': False, 'model': 'small'})
        with self.assertRaises(GenerationError) as caught:
            self.service.submit_export(self.clip_payload(auto_subtitles=True))
        self.assertEqual(caught.exception.status, 400)
        self.assertIn('faster-whisper', str(caught.exception))
        self.assertEqual(self.service.list_jobs(), [])

    def test_invalid_auto_flag_is_rejected_as_400(self):
        from modules.media_pipeline import MediaError
        with self.assertRaises(MediaError) as caught:
            self.service.submit_export(self.clip_payload(auto_subtitles='yes'))
        self.assertEqual(caught.exception.status, 400)

    def test_auto_flag_and_language_pass_through_to_pipeline(self):
        captured = {}
        real_export = self.service.pipeline.export

        def fake_export(payload, cancel=None):
            captured.update(payload)
            return {'kind': 'video', 'url': '/studio-media/fake.mp4', 'meta': {'subtitles': True}}

        self.service.pipeline.export = fake_export
        try:
            submitted = self.service.submit_export(self.clip_payload(auto_subtitles=True, subtitle_language='ZH'))
            job = self.wait_job(submitted['id'])
            self.assertEqual(job['status'], 'succeeded', job)
            self.assertIs(captured.get('auto_subtitles'), True)
            self.assertEqual(captured.get('subtitle_language'), 'zh')
            self.assertEqual(job['outputs'][0]['meta']['subtitles'], True)
        finally:
            self.service.pipeline.export = real_export

    def test_cancel_event_reaches_transcriber_between_segments(self):
        events = []

        def transcriber(audio, language, cancel):
            events.append(cancel)
            return [{'start': 0, 'end': 1, 'text': 'x'}]

        self.service.pipeline.transcriber = transcriber
        cancel = threading.Event()
        cancel.set()
        from modules.media_pipeline import MediaError
        with self.assertRaises(MediaError) as caught:
            self.service.pipeline._transcribe_joined(
                Path(self.temp.name) / 'joined.mp4', Path(self.temp.name), 5, '', cancel)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(len(events), 0, '取消时不应进入转写')


if __name__ == '__main__':
    unittest.main()
