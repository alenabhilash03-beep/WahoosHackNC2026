import copy
from pathlib import Path
import tempfile
import unittest
import io
import wave
from fastapi.testclient import TestClient
from server.app.main import create_app
from server.app.settings import Settings
from test_feedback import SAMPLE, speech


class Repository:
    def __init__(self):
        self.rows = [speech()]
        self.signed = []
        self.uploads = []
    def resolve_token(self, token):
        if token != 'good':
            raise ValueError('bad')
        return 'owner'
    def profile(self, user):
        return {'id': user, 'display_name': 'player'}
    def prompts(self):
        return [{'id': 'm', 'text': 'Motion', 'featured': True}]
    def speeches(self, user):
        return [copy.deepcopy(r) for r in self.rows if r['player_id'] == user]
    def speech(self, id, user):
        return next((copy.deepcopy(r) for r in self.rows if r['id'] == id and r['player_id'] == user), None)
    def signed_audio(self, path):
        self.signed.append(path)
        return 'https://private.example/signed'
    def upload(self, path, data, content_type):
        self.uploads.append((path, data, content_type))
    def create_speech(self, row):
        self.rows.append(dict(row, created_at='2026-10-10T12:01:00Z'))
    def update_speech(self, id, values):
        next(row for row in self.rows if row['id'] == id).update(values)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_dir=Path(self.temp.name))
        self.client = TestClient(create_app(self.settings, self.repo, lambda *args: copy.deepcopy(SAMPLE)))
        self.headers = {'Authorization': 'Bearer good'}
    def tearDown(self):
        self.temp.cleanup()
    def test_missing_invalid_auth_rejected(self):
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.assertEqual(self.client.get('/api/me', headers={'Authorization': 'Bearer wrong'}).status_code, 401)

    def test_public_config_supports_account_and_api_clients_without_secrets(self):
        settings = Settings(supabase_url='https://example.supabase.co',
                            supabase_anon_key='sb_publishable_test',
                            supabase_service_role_key='private-secret')
        client = TestClient(create_app(settings, self.repo))
        response = client.get('/config.js')
        self.assertIn('SUPABASE_PUBLISHABLE_KEY', response.text)
        self.assertIn('SUPABASE_ANON_KEY', response.text)
        self.assertNotIn('private-secret', response.text)
        self.assertEqual(client.get('/config.local.js').status_code, 200)
        self.assertNotIn('ARENA_CONFIG =', client.get('/config.local.js').text)
    def test_owner_only_private_audio_no_cross_user_leak(self):
        self.repo.rows.append(dict(speech('private'), player_id='other'))
        self.assertEqual(self.client.get('/api/speeches/private', headers=self.headers).status_code, 404)
        self.assertEqual(self.repo.signed, [])
        result = self.client.get('/api/speeches/current', headers=self.headers).json()
        self.assertEqual(result['audio_url'], 'https://private.example/signed')
        self.assertEqual(self.repo.signed, ['owner/audio.wav'])
        self.assertEqual(len(self.client.get('/api/me', headers=self.headers).json()['speeches']), 1)
    def test_upload_validates_side_prompt_type_size_and_grades(self):
        files = {'audio': ('take.wav', b'RIFFaudio', 'audio/wav')}
        self.assertEqual(self.client.post('/api/speeches', headers=self.headers, data={'motion_id': 'm', 'side': 'perhaps'}, files=files).status_code, 422)
        self.assertEqual(self.client.post('/api/speeches', headers=self.headers, data={'motion_id': 'missing', 'side': 'yes'}, files=files).status_code, 404)
        self.assertEqual(self.client.post('/api/speeches', headers=self.headers, data={'motion_id': 'm', 'side': 'yes'}, files={'audio': ('x.txt', b'x', 'text/plain')}).status_code, 415)
        response = self.client.post('/api/speeches', headers=self.headers, data={'motion_id': 'm', 'side': 'yes'}, files=files)
        self.assertEqual(response.status_code, 202)
        row = self.repo.rows[-1]
        self.assertEqual(row['player_id'], 'owner')
        self.assertEqual(row['status'], 'done')
        self.assertEqual(row['report'], SAMPLE)
        self.assertTrue(self.repo.uploads[0][0].startswith('owner/'))
    def test_grading_failure_is_private_generic_and_durable(self):
        def fail(*args):
            raise RuntimeError('secret API key detail')
        client = TestClient(create_app(self.settings, self.repo, fail))
        response = client.post('/api/speeches', headers=self.headers, data={'motion_id': 'm', 'side': 'no'}, files={'audio': ('a.wav', b'RIFFa', 'audio/wav')})
        self.assertEqual(response.status_code, 202)
        row = self.repo.rows[-1]
        self.assertEqual(row['status'], 'failed')
        self.assertNotIn('secret', row['error'])
    def test_demo_is_loopback_only_marked_persistent_and_no_fake_upload(self):
        settings = Settings(demo_mode=True, data_dir=Path(self.temp.name))
        app = create_app(settings)
        client = TestClient(app, base_url='http://localhost', client=('127.0.0.1', 3456))
        headers = {'Authorization': 'Bearer local-demo', 'X-Demo-Mode': 'true'}
        response = client.get('/api/me', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['demo'])
        self.assertTrue((Path(self.temp.name) / 'demo.json').exists())
        self.assertEqual(client.post('/api/speeches', headers=headers, data={'motion_id': 'demo-ai-pacing', 'side': 'yes'}, files={'audio': ('a.wav', b'abc', 'audio/wav')}).status_code, 503)
        self.assertEqual(client.get('/api/me', headers={**headers, 'Host': 'evil.example'}).status_code, 403)
        self.assertEqual(client.get('/api/me', headers={**headers, 'Origin': 'https://evil.example'}).status_code, 403)
        public = client.get('/config.js').text
        self.assertIn('DEMO_MODE', public)
        self.assertNotIn('SERVICE_ROLE', public)
    def test_grader_ungradable_does_not_count_as_zero_grade(self):
        client = TestClient(create_app(self.settings, self.repo, lambda *args: {'gradable': False, 'overall': {'score': 0, 'letter': 'F'}, 'reason': 'Too short', 'factors': {}}))
        response = client.post('/api/speeches', headers=self.headers, data={'motion_id': 'm', 'side': 'yes'}, files={'audio': ('a.wav', b'abc', 'audio/wav')})
        row = self.repo.rows[-1]
        self.assertEqual(response.status_code, 202)
        self.assertEqual(row['status'], 'ungradable')
        self.assertIsNone(row['overall'])

    def test_size_empty_and_supplied_player_id(self):
        self.settings.max_audio_bytes = 8
        oversized = self.client.post('/api/speeches', headers=self.headers, data={'motion_id': 'm', 'side': 'yes'}, files={'audio': ('a.wav', b'123456789', 'audio/wav')})
        self.assertEqual(oversized.status_code, 413)
        empty = self.client.post('/api/speeches', headers=self.headers, data={'motion_id': 'm', 'side': 'yes'}, files={'audio': ('a.wav', b'', 'audio/wav')})
        self.assertEqual(empty.status_code, 422)
        response = self.client.post('/api/speeches', headers=self.headers, data={'motion_id': 'm', 'side': 'yes', 'player_id': 'victim'}, files={'audio': ('a.wav', b'123', 'audio/wav')})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.repo.rows[-1]['player_id'], 'owner')

    def test_provider_failures_return_generic_errors(self):
        self.repo.signed_audio = lambda path: (_ for _ in ()).throw(RuntimeError('secret storage URL'))
        client = TestClient(create_app(self.settings, self.repo), raise_server_exceptions=False)
        response = client.get('/api/speeches/current', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', response.text)

    def test_demo_rejects_remote_peer_and_non_demo_token(self):
        app = create_app(Settings(demo_mode=True, data_dir=Path(self.temp.name)))
        headers = {'Authorization': 'Bearer local-demo'}
        remote = TestClient(app, base_url='http://localhost', client=('192.0.2.1', 1234))
        self.assertEqual(remote.get('/api/me', headers=headers).status_code, 403)
        local = TestClient(app, base_url='http://localhost', client=('127.0.0.1', 1234))
        self.assertEqual(local.get('/api/me', headers={'Authorization': 'Bearer ordinary-token'}).status_code, 401)

    def test_demo_synthetic_word_timings_fit_matching_fixture_audio(self):
        app = create_app(Settings(demo_mode=True, data_dir=Path(self.temp.name)))
        client = TestClient(app, base_url='http://localhost', client=('127.0.0.1', 1234))
        card = client.get('/api/speeches/demo-best', headers={'Authorization': 'Bearer local-demo'}).json()
        words = card['report']['words']
        self.assertEqual(len(words), 155)
        self.assertTrue(any(word['text'].strip(',.') == 'um' for word in words))
        content = client.get(card['audio_url']).content
        with wave.open(io.BytesIO(content)) as audio:
            duration = audio.getnframes() / audio.getframerate()
        self.assertGreater(duration, words[-1]['end'])
        self.assertIn('synthetic', card['report']['demo']['timing'].lower())


if __name__ == '__main__':
    unittest.main()
