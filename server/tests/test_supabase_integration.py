"""Real Supabase round trip: the actual SDK, Auth, PostgREST and private Storage.

Runs only against a disposable local stack (`supabase start`) with
SUPABASE_INTEGRATION_TEST=true, and refuses any non-loopback URL so it can never
create accounts or rows in the hosted project. Only provider grading is injected.
It creates two temporary accounts and removes them, their speeches and their audio.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlparse
from uuid import uuid4
import wave

from fastapi.testclient import TestClient
import httpx

from server.app.main import create_app
from server.app.settings import Settings
from server.scripts import recover_grading

# Read the sample directly: importing it from test_feedback would pull in the grader's NumPy stack.
SAMPLE = json.loads((Path(__file__).resolve().parents[2] / 'softskills-grader/sample_report.json').read_text(encoding='utf-8'))
ENABLED = os.getenv('SUPABASE_INTEGRATION_TEST', '').lower() == 'true'
PASSWORD = 'integration-test-only-' + uuid4().hex


def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\x00\x01' * 4000)
    return buffer.getvalue()


@unittest.skipUnless(ENABLED, 'Set SUPABASE_INTEGRATION_TEST=true against a local `supabase start` stack.')
class SupabaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from supabase import create_client
        from server.app.repository import SupabaseRepository
        url, anon, service = (os.environ[key] for key in ('SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY'))
        if urlparse(url).hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise RuntimeError('Refusing to run: SUPABASE_URL is not a local disposable stack.')
        cls.temp = tempfile.TemporaryDirectory()
        cls.settings = Settings(supabase_url=url, supabase_anon_key=anon, supabase_service_role_key=service,
                                data_dir=Path(cls.temp.name))
        cls.repository = SupabaseRepository(cls.settings)
        cls.admin = create_client(url, service)       # account setup and cleanup only
        cls.users, cls.motions = {}, []
        for name in ('owner', 'other'):
            email = f'{name}-{uuid4().hex}@example.com'
            user = cls.admin.auth.admin.create_user({'email': email, 'password': PASSWORD, 'email_confirm': True}).user
            # A separate public client per account: signing in on the service client would replace its key.
            session = create_client(url, anon).auth.sign_in_with_password({'email': email, 'password': PASSWORD}).session
            cls.users[name] = dict(id=user.id, headers={'Authorization': 'Bearer ' + session.access_token})
        cls.audio = wav()
        cls.outcome = {'fail': False}

        def grader(path, prompt, side, cache_dir):
            if cls.outcome['fail']:
                raise RuntimeError('provider down')
            assert Path(path).read_bytes() == cls.audio
            return copy.deepcopy(SAMPLE)

        cls.client = TestClient(create_app(cls.settings, cls.repository, grader))
        cls.motion = cls.repository.prompts()[0]['id']

    @classmethod
    def tearDownClass(cls):
        bucket = cls.admin.storage.from_('audio')
        for user in cls.users.values():
            paths = [f"{user['id']}/{item['name']}" for item in bucket.list(user['id'])]
            if paths:
                bucket.remove(paths)
            cls.admin.table('speeches').delete().eq('player_id', user['id']).execute()
            cls.admin.table('profiles').delete().eq('id', user['id']).execute()
            cls.admin.auth.admin.delete_user(user['id'])
        for motion in cls.motions:
            cls.admin.table('motions').delete().eq('id', motion).execute()
        cls.temp.cleanup()

    def upload(self, who='owner'):
        response = self.client.post('/api/speeches', headers=self.users[who]['headers'],
                                    data={'motion_id': self.motion, 'side': 'yes'},
                                    files={'audio': ('take.wav', self.audio, 'audio/wav')})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['speech_id']

    def card(self, id, who='owner'):
        return self.client.get('/api/speeches/' + id, headers=self.users[who]['headers'])

    def stored(self, id):
        return self.admin.table('speeches').select('*').eq('id', id).execute().data[0]

    def test_account_tokens_are_verified_by_auth(self):
        owner = self.users['owner']
        self.assertEqual(self.client.get('/api/me', headers=owner['headers']).json()['id'], owner['id'])
        self.assertEqual(self.client.get('/api/me', headers={'Authorization': 'Bearer not.a.token'}).status_code, 401)
        self.assertEqual(self.client.get('/api/me', headers={'Authorization': 'Bearer ' + self.settings.supabase_anon_key}).status_code, 401)

    def test_unreachable_auth_is_503_not_a_sign_out(self):
        from server.app.repository import SupabaseRepository
        down = Settings(supabase_url='http://127.0.0.1:9', supabase_anon_key=self.settings.supabase_anon_key,
                        supabase_service_role_key=self.settings.supabase_service_role_key, data_dir=Path(self.temp.name))
        client = TestClient(create_app(down, SupabaseRepository(down)))
        response = client.get('/api/me', headers=self.users['owner']['headers'])
        self.assertEqual(response.status_code, 503, response.text)

    def test_speech_persists_with_private_audio_and_is_owner_only(self):
        from server.app.repository import SupabaseRepository
        id = self.upload()
        card = self.card(id).json()
        self.assertEqual((card['status'], card['report']['overall'], card['can_retry']), ('done', SAMPLE['overall'], False))
        # The recording is reachable only through its short-lived signed link.
        self.assertEqual(httpx.get(card['audio_url']).content, self.audio)
        path = self.stored(id)['audio_path']
        self.assertTrue(path.startswith(self.users['owner']['id'] + '/'))
        public = httpx.get(f"{self.settings.supabase_url}/storage/v1/object/public/audio/{path}")
        self.assertGreaterEqual(public.status_code, 400)
        # A new server process (fresh client) still sees it: it is in the database, not in memory.
        again = SupabaseRepository(self.settings).speech(id, self.users['owner']['id'])
        self.assertEqual((again['status'], float(again['overall'])), ('done', SAMPLE['overall']['score']))
        self.assertIn(id, [s['id'] for s in self.client.get('/api/me', headers=self.users['owner']['headers']).json()['speeches']])
        # Another account cannot open, retry or list it.
        self.assertEqual(self.card(id, 'other').status_code, 404)
        self.assertEqual(self.client.post(f'/api/speeches/{id}/retry', headers=self.users['other']['headers']).status_code, 404)
        self.assertEqual(self.client.get('/api/me', headers=self.users['other']['headers']).json()['speeches'], [])

    def test_display_names_conflict_across_accounts_ignoring_case(self):
        name = 'Ada_' + uuid4().hex[:8]
        post = lambda who, value: self.client.post('/api/me/name', headers=self.users[who]['headers'], json={'display_name': value})
        self.assertEqual(post('owner', name).status_code, 200)
        self.assertEqual(post('other', name.upper()).status_code, 409)
        self.assertEqual(post('owner', name.lower()).status_code, 200)      # renaming yourself is not a clash

    def test_failed_speech_is_claimed_once_and_retried_from_saved_audio(self):
        owner, other = self.users['owner']['id'], self.users['other']['id']
        self.outcome['fail'] = True
        try:
            id = self.upload()
        finally:
            self.outcome['fail'] = False
        before = self.stored(id)
        self.assertEqual(before['status'], 'failed')
        self.assertTrue(self.card(id).json()['can_retry'])
        self.assertIsNone(self.repository.claim_retry(id, other))           # not the owner's
        with ThreadPoolExecutor(8) as pool:
            claims = list(pool.map(lambda _: self.repository.claim_retry(id, owner), range(8)))
        self.assertEqual(len([claim for claim in claims if claim]), 1)      # one winner among 8 simultaneous retries
        self.assertEqual(self.stored(id)['status'], 'queued')
        self.assertEqual(self.client.post(f'/api/speeches/{id}/retry', headers=self.users['owner']['headers']).status_code, 409)
        # An interrupted grading is recovered to failed, then retried through the API.
        done = self.upload()
        self.assertEqual(recover_grading.recover(self.repository, apply=True), (1, 1))
        self.assertEqual((self.stored(id)['status'], self.stored(done)['status']), ('failed', 'done'))
        response = self.client.post(f'/api/speeches/{id}/retry', headers=self.users['owner']['headers'])
        self.assertEqual(response.status_code, 202, response.text)
        after = self.stored(id)
        self.assertEqual((after['status'], after['error'], after['report']['overall']), ('done', None, SAMPLE['overall']))
        self.assertEqual({key: after[key] for key in ('id', 'created_at', 'audio_path', 'player_id')},
                         {key: before[key] for key in ('id', 'created_at', 'audio_path', 'player_id')})

    def test_three_difficulty_boards_rank_best_scores_from_the_real_database(self):
        owner, other = self.users['owner'], self.users['other']
        tag = uuid4().hex[:6]
        names = {'owner': f'Own_{tag}', 'other': f'Oth_{tag}'}
        for who, name in names.items():
            self.repository.set_display_name(self.users[who]['id'], name)
        levels = {level: self.repository.create_motion(f'Should board test {tag} {level} pass?', category='Tests', difficulty=level)
                  for level in ('beginner', 'intermediate', 'expert')}
        self.motions.extend(levels.values())

        def speech(who, motion, score):
            response = self.client.post('/api/speeches', headers=self.users[who]['headers'], data={'motion_id': motion, 'side': 'yes'},
                                        files={'audio': ('take.wav', self.audio, 'audio/wav')})
            self.assertEqual(response.status_code, 202, response.text)
            self.admin.table('speeches').update({'overall': score}).eq('id', response.json()['speech_id']).execute()
            return response.json()['speech_id']

        speech('owner', levels['expert'], 90)
        speech('other', levels['expert'], 95)
        speech('owner', levels['beginner'], 70)
        speech('owner', levels['beginner'], 80)                # a second try: the best counts
        speech('owner', self.motion, 99)                       # seed prompt, no difficulty: on no board
        ungraded = speech('other', levels['intermediate'], 88)
        self.admin.table('speeches').update({'status': 'ungradable', 'overall': None}).eq('id', ungraded).execute()

        result = self.client.get('/api/leaderboard', headers=owner['headers']).json()
        self.assertEqual(list(result['boards']), ['beginner', 'intermediate', 'expert'])
        self.assertEqual(result['display_name'], names['owner'])
        mine = lambda level: [row for row in result['boards'][level]['rows'] if row['display_name'] in names.values()]
        letter = SAMPLE['overall']['letter']
        self.assertEqual(mine('expert'), [
            dict(place=mine('expert')[0]['place'], display_name=names['other'], score=95, letter=letter, attempts=1, you=False),
            dict(place=mine('expert')[1]['place'], display_name=names['owner'], score=90, letter=letter, attempts=1, you=True)])
        self.assertLess(mine('expert')[0]['place'], mine('expert')[1]['place'])
        self.assertEqual([(row['display_name'], row['score'], row['attempts'], row['you']) for row in mine('beginner')],
                         [(names['owner'], 80, 2, True)])
        self.assertEqual(mine('intermediate'), [])             # an ungradable speech is not a score
        self.assertEqual(result['boards']['expert']['you']['score'], 90)
        self.assertIsNone(result['boards']['intermediate']['you'])
        self.assertNotIn(99, [row['score'] for level in result['boards'].values() for row in level['rows']])
        theirs = self.client.get('/api/leaderboard', headers=other['headers']).json()
        self.assertEqual((theirs['boards']['expert']['you']['score'], theirs['boards']['beginner']['you']), (95, None))
        for private in (owner['id'], other['id'], 'transcript', 'audio_path'):
            self.assertNotIn(private, self.client.get('/api/leaderboard', headers=other['headers']).text)

    def test_concurrent_identical_questions_share_one_motion(self):
        text = f'Should integration test {uuid4().hex} pass?'
        with ThreadPoolExecutor(8) as pool:
            ids = list(pool.map(lambda _: self.repository.create_motion(text, category='Tests', difficulty='beginner'), range(8)))
        self.motions.append(ids[0])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.admin.table('motions').select('id').eq('text', text).execute().data), 1)


if __name__ == '__main__':
    unittest.main()
