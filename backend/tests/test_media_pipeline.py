import importlib.util
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path


class MediaPipelineTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('modules.media_pipeline'), 'local media pipeline must exist')
        from modules.media_pipeline import MediaPipeline
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = Path(self.temp.name) / 'studio-media'
        self.media.mkdir()
        self.pipeline = MediaPipeline(self.media)

    def test_local_reference_resolution_rejects_external_and_path_escape(self):
        from modules.media_pipeline import MediaError
        (self.media / 'clip.mp4').write_bytes(b'media')
        self.assertEqual(self.pipeline.resolve_media('/studio-media/clip.mp4'), self.media / 'clip.mp4')
        for url in ('https://example.com/clip.mp4', '/studio-media/../secret', '/studio-media/%2e%2e%2fsecret', 'file:///secret', '/studio-media/a\\..\\secret'):
            with self.assertRaises(MediaError):
                self.pipeline.resolve_media(url)

    def test_invalid_export_parameters_are_rejected_before_ffmpeg(self):
        from modules.media_pipeline import MediaError
        for payload in ({'clips': []}, {'clips': [{'url': '/studio-media/missing.mp4'}]},
                        {'clips': [{}]}, {'clips': [{'url': '/studio-media/a.mp4'}], 'resolution': '9000x9000'}):
            with self.assertRaises(MediaError):
                self.pipeline.validate_export(payload)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg unavailable')
    def test_two_real_clips_trim_subtitles_mix_audio_and_keep_order(self):
        for index, color in enumerate(('red', 'blue')):
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={color}:s=160x90:r=12:d=1.2',
                            '-f', 'lavfi', '-i', f'sine=frequency={440+index*220}:duration=1.2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(self.media / f'clip{index}.mp4')], check=True, capture_output=True)
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=200:duration=3', str(self.media / 'music.wav')], check=True, capture_output=True)
        result = self.pipeline.export({'clips': [{'url': '/studio-media/clip0.mp4', 'trim_start': .2, 'trim_end': 1, 'subtitle': '第一镜头'},
                                                 {'url': '/studio-media/clip1.mp4', 'trim_start': .2, 'trim_end': 1, 'subtitle': 'Second shot'}],
                                       'audio_urls': ['/studio-media/music.wav'], 'resolution': '320x180', 'fps': 12})
        path = self.media / Path(result['url']).name
        self.assertTrue(path.is_file())
        probe = self.pipeline.probe(path)
        self.assertAlmostEqual(float(probe['format']['duration']), 1.6, delta=.2)
        streams = probe['streams']
        self.assertIn('audio', [stream['codec_type'] for stream in streams])
        video = next(stream for stream in streams if stream['codec_type'] == 'video')
        self.assertEqual((video['width'], video['height']), (320, 180))
        subtitle_stream = next(stream for stream in streams if stream['codec_type'] == 'subtitle')
        self.assertEqual(subtitle_stream['codec_name'], 'mov_text')
        subtitles = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:s:0', '-f', 'srt', '-'], check=True, capture_output=True).stdout.decode('utf-8')
        self.assertIn('第一镜头', subtitles)
        self.assertIn('Second shot', subtitles)
        for second, channel in ((.2, 0), (1.2, 2)):
            frame = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(second), '-i', str(path), '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], check=True, capture_output=True).stdout
            self.assertGreater(frame[channel], 150, frame)


if __name__ == '__main__':
    unittest.main()
