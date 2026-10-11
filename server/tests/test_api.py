import copy
from pathlib import Path
import tempfile
import unittest
import io
import wave
from fastapi.testclient import TestClient
from server.app.leaderboard import NameTaken, rank
from server.app.main import create_app
from server.app.session import InvalidSession
from server.app.settings import Settings
from test_feedback import SAMPLE, speech


class Repository:
    def __init__(self):
        self.rows = [speech()]
        self.names = {}
        self.signed = []
        self.uploads = []
        self.downloads = []
    def resolve_token(self, token):
        if token != 'good':
            raise InvalidSession('bad')
        return 'owner'
    def profile(self, user):
        return {'id': user, 'display_name': self.names.get(user, 'player')}
    def set_display_name(self, user, name):
        if any(other != user and taken.lower() == name.lower() for other, taken in self.names.items()):
            raise NameTaken(name)
        self.names[user] = name
    def board_rows(self):
        return [dict(player_id=r['player_id'], display_name=self.names[r['player_id']], score=r['report']['overall']['score'],
                     letter=r['report']['overall']['letter'], created_at=r['created_at'])
                for r in self.rows if r['player_id'] in self.names and r.get('report')]
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
    def download(self, path):
        self.downloads.append(path)
        return next(data for stored, data, _ in self.uploads if stored == path)
    def claim_retry(self, id, user):
        row = next((r for r in self.rows if r['id'] == id and r['player_id'] == user and r['status'] == 'failed'), None)
        if row:
            row.update(status='queued', error=None)
        return copy.deepcopy(row)


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
    def test_auth_provider_outage_is_a_generic_503_not_a_sign_out(self):
        def outage(token):
            raise ConnectionError('secret auth host unreachable')
        self.repo.resolve_token = outage
        response = self.client.get('/api/me', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', response.text)
        self.assertNotIn('WWW-Authenticate', response.headers)
    def test_health_reports_liveness_without_an_account_or_the_database(self):
        client = TestClient(create_app(Settings(data_dir=Path(self.temp.name))))   # no Supabase credentials
        response = client.get('/api/health')
        self.assertEqual((response.status_code, response.json()), (200, {'status': 'ok'}))
        self.assertEqual(client.get('/api/me', headers=self.headers).status_code, 503)
    def test_pages_origin_preflight_and_errors_carry_cors_headers(self):
        origin = 'https://alenabhilash03-beep.github.io'
        client = TestClient(create_app(Settings(data_dir=Path(self.temp.name), cors_origins=[origin]), self.repo))
        ask = lambda method: client.options('/api/speeches/current/retry', headers={
            'Origin': origin, 'Access-Control-Request-Method': method, 'Access-Control-Request-Headers': 'authorization'})
        self.assertEqual((ask('POST').status_code, ask('POST').headers['access-control-allow-origin']), (200, origin))
        self.assertEqual(ask('DELETE').status_code, 400)
        rejected = client.get('/api/me', headers={'Origin': origin, 'Authorization': 'Bearer wrong'})
        self.assertEqual((rejected.status_code, rejected.headers['access-control-allow-origin']), (401, origin))
        other = client.options('/api/me', headers={'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'GET'})
        self.assertNotIn('access-control-allow-origin', other.headers)
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
        card = client.get('/api/speeches/' + row['id'], headers=self.headers).json()
        self.assertEqual((card['status'], card['can_retry']), ('failed', True))
        self.assertFalse(client.get('/api/speeches/current', headers=self.headers).json()['can_retry'])
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

    def test_name_rules_and_taken_names(self):
        post = lambda name: self.client.post('/api/me/name', headers=self.headers, json={'display_name': name})
        self.assertEqual(self.client.post('/api/me/name', json={'display_name': 'Ada_1'}).status_code, 401)
        for bad in ('ab', 'x' * 21, 'two words', '<b>bold</b>', 'player', 'Player'):
            self.assertEqual(post(bad).status_code, 422, bad)
        self.repo.names['other'] = 'Grace'
        self.assertEqual(post('grace').status_code, 409)
        self.assertEqual(post(' Ada_1 ').json(), {'display_name': 'Ada_1'})
        self.assertEqual(post('ada_1').status_code, 200)   # renaming yourself is not a clash
        self.assertEqual(self.repo.names['owner'], 'ada_1')
    def test_leaderboard_lists_named_players_by_best_score_without_private_data(self):
        low = copy.deepcopy(SAMPLE)
        low['overall'] = {'score': 61.5, 'letter': 'D-'}
        self.repo.rows += [dict(speech('weak', at='2026-10-10T09:00:00Z', report=low)),
                           dict(speech('theirs', at='2026-10-10T08:00:00Z', report=low), player_id='other'),
                           dict(speech('hidden'), player_id='unnamed')]
        board = self.client.get('/api/leaderboard', headers=self.headers).json()
        self.assertEqual(board, {'rows': [], 'you': None, 'display_name': None})   # nobody has joined
        self.repo.names.update(owner='Ada_1', other='Grace')
        board = self.client.get('/api/leaderboard', headers=self.headers).json()
        best = SAMPLE['overall']
        self.assertEqual(board['rows'], [
            dict(place=1, display_name='Ada_1', score=best['score'], letter=best['letter'], attempts=2, you=True),
            dict(place=2, display_name='Grace', score=61.5, letter='D-', attempts=1, you=False)])
        self.assertEqual(board['you'], board['rows'][0])
        self.assertEqual(board['display_name'], 'Ada_1')
        self.assertEqual(self.client.get('/api/leaderboard').status_code, 401)
    def test_rank_breaks_ties_by_earlier_speech_and_keeps_your_row_past_the_limit(self):
        row = lambda player, score, at: dict(player_id=player, display_name=player.upper(), score=score, letter='B', created_at=at)
        board = rank([row('late', 80, '2026-10-10T12:00:00Z'), row('early', 80, '2026-10-10T11:00:00Z'),
                      row('early', 70, '2026-10-10T10:00:00Z'), row('last', 50, '2026-10-10T09:00:00Z')], 'last', limit=2)
        self.assertEqual([(r['place'], r['display_name'], r['attempts']) for r in board['rows']], [(1, 'EARLY', 2), (2, 'LATE', 1)])
        self.assertEqual((board['you']['place'], board['you']['score']), (3, 50))
    def test_demo_board_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            client = TestClient(create_app(Settings(demo_mode=True, data_dir=Path(directory))), base_url='http://localhost', client=('127.0.0.1', 1234))
            headers = {'Authorization': 'Bearer local-demo'}
            board = client.get('/api/leaderboard', headers=headers).json()
            self.assertTrue(board['demo'])
            self.assertEqual([(r['display_name'], r['score'], r['attempts']) for r in board['rows']], [('Local demo', SAMPLE['overall']['score'], 2)])
            self.assertEqual(client.post('/api/me/name', headers=headers, json={'display_name': 'Ada_1'}).status_code, 503)
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
