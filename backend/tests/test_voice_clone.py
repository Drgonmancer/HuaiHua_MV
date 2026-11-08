import base64
import io
import json
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from flask import Flask

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1sAAAAASUVORK5CYII=')


def wav_bytes(frames=b'\0\x00' * 800, framerate=8000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, framerate, 0, 'NONE', 'not compressed'))
        wav.writeframes(frames)
    return buffer.getvalue()


class VoiceCloneReferenceTests(unittest.TestCase):
    def setUp(self):
        from modules.studio_service import StudioService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'canvas.db'
        self.studio = StudioService(self.db)
        self.media = self.studio.media_dir

    def store_asset(self, name, data, suffix, category):
        asset_id = 'asset-' + name
        filename = asset_id + suffix
        (self.media / filename).write_bytes(data)
        with self.studio._connection() as conn:
            conn.execute('INSERT INTO studio_assets (id,name,category,media_type,filename,mime_type,created_at) VALUES (?,?,?,?,?,?,?)',
                         (asset_id, name, category, 'audio' if suffix == '.wav' else 'image', filename,
                          'audio/wav' if suffix == '.wav' else 'image/png', 'now'))
        return asset_id

    def test_normalize_keeps_voice_reference_fields(self):
        from modules.studio_service import normalize_production
        result = normalize_production({'characters': [{'id': 'c1', 'name': '主角', 'voice_reference_asset_id': 'asset-voice',
                                                       'voice_reference_text': '你好，这是参考音频。'}]})
        character = result['characters'][0]
        self.assertEqual(character['voice_reference_asset_id'], 'asset-voice')
        self.assertEqual(character['voice_reference_text'], '你好，这是参考音频。')

    def test_voice_reference_must_be_audio(self):
        from modules.studio_service import StudioValidationError
        image_id = self.store_asset('face', PNG, '.png', 'character')
        production = {'characters': [{'id': 'c1', 'name': '主角', 'reference_asset_ids': [], 'voice_reference_asset_id': image_id}], 'scenes': []}
        with self.assertRaises(StudioValidationError) as caught:
            self.studio.validate_references(production)
        self.assertIn('音频', str(caught.exception))

    def test_voice_reference_accepts_audio_asset(self):
        audio_id = self.store_asset('voice', wav_bytes(), '.wav', 'audio')
        production = {'characters': [{'id': 'c1', 'name': '主角', 'reference_asset_ids': [], 'voice_reference_asset_id': audio_id}], 'scenes': []}
        self.studio.validate_references(production)  # must not raise

    def test_storyboard_wires_voice_reference_audio_to_speech_nodes(self):
        audio_id = self.store_asset('voice', wav_bytes(), '.wav', 'audio')
        production = {'kind': 'comic', 'brief': '建立角色', 'characters': [
            {'id': 'c1', 'name': '主角', 'description': '少年', 'reference_asset_ids': [],
             'voice_reference_asset_id': audio_id, 'voice_reference_text': '你好，这是参考音频。'}],
            'scenes': [], 'episodes': [], 'shots': []}
        graph = self.studio.build_storyboard(production, source='template')
        audio_nodes = [node for node in graph['nodes'] if node['type'] == 'audio-gen']
        self.assertTrue(audio_nodes)
        voice_sources = {node['id']: node for node in graph['nodes'] if node['type'] == 'asset' and node['data'].get('mediaType') == 'audio'}
        for audio in audio_nodes:
            incoming = [edge for edge in graph['edges'] if edge['target'] == audio['id'] and edge['targetHandle'] == 'in:ref']
            self.assertEqual(len(incoming), 1, '每个配音节点都应连接角色参考音频')
            self.assertIn(incoming[0]['source'], voice_sources)
            self.assertIn('voiceReferenceText', audio['data']['params'])

    def test_storyboard_graph_validation_passes_for_voice_edges(self):
        audio_id = self.store_asset('voice', wav_bytes(), '.wav', 'audio')
        production = {'kind': 'comic', 'brief': '建立角色', 'characters': [
            {'id': 'c1', 'name': '主角', 'voice_reference_asset_id': audio_id}],
            'scenes': [], 'episodes': [], 'shots': []}
        graph = self.studio.build_storyboard(production, source='template')
        self.studio.validate_graph(graph['nodes'], graph['edges'])


class VoiceCloneGenerationTests(unittest.TestCase):
    def setUp(self):
        from modules.generation_service import GenerationService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'canvas.db'
        self.settings = {'AUDIO_BASE_URL': 'http://127.0.0.1:9880', 'AUDIO_API_KEY': 'local',
                         'AUDIO_MODEL': 'gpt-sovits', 'AUDIO_PROTOCOL': 'clone',
                         'AUDIO_TEXT_LANG': 'ZH', 'AUDIO_PROMPT_LANG': 'en'}
        self.service = GenerationService(self.db, settings=self.settings)
        self.addCleanup(self.service.close)
        (self.service.media_dir / 'voice.wav').write_bytes(wav_bytes())

    def payload(self, run='voice-run', with_ref=True):
        payload = {'node_type': 'audio-gen', 'node_id': 'audio-one', 'project_id': 'project-one',
                   'client_run_id': run, 'params': {'speed': 1, 'voiceReferenceText': '你好，这是参考音频。'},
                   'inputs': {'text': ['今天天气很好。']}}
        if with_ref:
            payload['inputs']['ref'] = ['/studio-media/voice.wav']
        return payload

    def wait_job(self, job_id):
        for _ in range(150):
            job = self.service.get_job(job_id)
            if job['status'] in ('succeeded', 'failed', 'canceled'):
                return job
            time.sleep(.02)
        self.fail('background job did not finish')

    def test_audio_meta_reports_cloning_protocol(self):
        meta = self.service.audio_meta()
        self.assertTrue(meta['cloning'])
        self.assertEqual(meta['protocol'], 'clone')
        self.assertEqual(meta['voices'], [])

    def test_clone_submit_uses_gptsovits_contract_and_persists_audio(self):
        wav = wav_bytes()
        with patch.object(self.service, '_request_binary', return_value=(wav, 'audio/wav')) as supplier:
            receipt = self.service.submit_node(self.payload())
            job = self.wait_job(receipt['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded', job)
            self.assertEqual(supplier.call_args.args[1], '/tts')
            request = supplier.call_args.kwargs['payload']
            self.assertEqual(request['text'], '今天天气很好。')
            self.assertEqual(request['text_lang'], 'zh', '语言配置应统一小写')
            self.assertEqual(request['prompt_lang'], 'en')
            self.assertEqual(request['prompt_text'], '你好，这是参考音频。')
            self.assertEqual(request['ref_audio_path'], str(self.service.media_dir / 'voice.wav'))
            self.assertEqual((self.service.media_dir / Path(job['outputs'][0]['url']).name).read_bytes(), wav)

    def test_clone_media_root_remaps_reference_path(self):
        from modules.generation_service import GenerationService
        remapped = GenerationService(self.db, settings={**self.settings, 'AUDIO_CLONE_MEDIA_ROOT': str(Path(self.temp.name) / 'mounted')})
        self.addCleanup(remapped.close)
        with patch.object(remapped, '_request_binary', return_value=(wav_bytes(), 'audio/wav')) as supplier:
            receipt = remapped.submit_node(self.payload(run='remap-run'))
            self.wait_job(receipt['jobs'][0]['id'])
            request = supplier.call_args.kwargs['payload']
            self.assertEqual(request['ref_audio_path'], str(Path(self.temp.name) / 'mounted' / 'voice.wav'))

    def test_clone_submit_without_reference_audio_is_rejected(self):
        from modules.generation_service import GenerationError
        with self.assertRaises(GenerationError) as caught:
            self.service.submit_node(self.payload(with_ref=False))
        self.assertEqual(caught.exception.status, 400)
        self.assertIn('参考音频', str(caught.exception))

    def test_openai_protocol_still_requires_configured_voice(self):
        from modules.generation_service import GenerationService, GenerationError
        openai_service = GenerationService(self.db, settings={'AUDIO_BASE_URL': 'https://speech.example/v1',
                                                              'AUDIO_API_KEY': 'k', 'AUDIO_MODEL': 'tts-1'})
        self.addCleanup(openai_service.close)
        payload = self.payload(run='openai-run')
        payload['inputs']['ref'] = ['/studio-media/voice.wav']
        payload['params']['voiceId'] = 'not-a-voice'
        with self.assertRaises(GenerationError) as caught:
            openai_service.submit_node(payload)
        self.assertIn('AUDIO_VOICES', str(caught.exception))

    def test_openai_protocol_tolerates_reference_audio_port(self):
        from modules.generation_service import GenerationService
        openai_service = GenerationService(self.db, settings={'AUDIO_BASE_URL': 'https://speech.example/v1',
                                                              'AUDIO_API_KEY': 'k', 'AUDIO_MODEL': 'tts-1'})
        self.addCleanup(openai_service.close)
        payload = self.payload(run='openai-ref-run')
        payload['inputs']['ref'] = ['/studio-media/voice.wav']
        with patch.object(openai_service, '_request_binary', return_value=(wav_bytes(), 'audio/wav')) as supplier:
            receipt = openai_service.submit_node(payload)
            job = self.wait_job(receipt['jobs'][0]['id'])
            self.assertEqual(job['status'], 'succeeded', job)
            self.assertEqual(supplier.call_args.args[1], '/audio/speech')


if __name__ == '__main__':
    unittest.main()
