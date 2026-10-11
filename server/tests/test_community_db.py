"""Community rounds and reactions against real Postgres, Storage and Auth (migration 0006).

Mocks cannot prove that votes are deduplicated, that two rounds cannot overlap, or that
the public keys cannot call the ranking and reaction functions. This does, with the real
SDK, on the same disposable local stack as test_supabase_integration.py and with the same
guard: SUPABASE_INTEGRATION_TEST=true and a loopback URL only. Only grading is injected.
It removes the accounts, speeches, audio and rounds it creates.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlparse
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
from postgrest.exceptions import APIError

from server.app import community
from server.app.community import EntryConflict
from server.app.main import create_app
from server.app.settings import Settings
from test_supabase_integration import ENABLED, PASSWORD, SAMPLE, wav


@unittest.skipUnless(ENABLED, 'Set SUPABASE_INTEGRATION_TEST=true against a local `supabase start` stack.')
class CommunityDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from supabase import create_client
        from server.app.repository import SupabaseRepository
        url, anon, service = (os.environ[key] for key in ('SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY'))
        if urlparse(url).hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise RuntimeError('Refusing to run: SUPABASE_URL is not a local disposable stack.')
        cls.url, cls.anon, cls.create_client = url, anon, staticmethod(create_client)
        cls.temp = tempfile.TemporaryDirectory()
        cls.settings = Settings(supabase_url=url, supabase_anon_key=anon, supabase_service_role_key=service,
                                data_dir=Path(cls.temp.name), community_enabled=True)
        cls.repository = SupabaseRepository(cls.settings)
        cls.admin = create_client(url, service)       # setup, direct constraint checks and cleanup only
        cls.users, cls.rounds = {}, []
        for name in ('ada', 'grace', 'linus', 'mallory'):
            email = f'{name}-{uuid4().hex}@example.com'
            user = cls.admin.auth.admin.create_user({'email': email, 'password': PASSWORD, 'email_confirm': True}).user
            session = create_client(url, anon).auth.sign_in_with_password({'email': email, 'password': PASSWORD}).session
            cls.users[name] = dict(id=user.id, token=session.access_token,
                                   headers={'Authorization': 'Bearer ' + session.access_token})
            cls.repository.profile(user.id)
            if name != 'mallory':                     # mallory never picks a display name
                cls.repository.set_display_name(user.id, f'{name.title()}_{uuid4().hex[:6]}')
        cls.audio = wav()
        cls.client = TestClient(create_app(cls.settings, cls.repository, lambda *args: copy.deepcopy(SAMPLE)))
        cls.motion = cls.repository.prompts()[0]['id']
        cls.featured_before = {row['id']: row['featured_on'] for row in
                               cls.admin.table('motions').select('id,featured_on').execute().data}
        now = datetime.now(timezone.utc)
        cls.round = cls.repository.create_round(cls.motion, now - timedelta(hours=1), now + timedelta(hours=1))
        assert cls.round, 'another round is already open in this disposable database'
        cls.rounds.append(cls.round)

    @classmethod
    def tearDownClass(cls):
        bucket = cls.admin.storage.from_('audio')
        for user in cls.users.values():
            paths = [f"{user['id']}/{item['name']}" for item in bucket.list(user['id'])]
            if paths:
                bucket.remove(paths)
            cls.admin.table('speeches').delete().eq('player_id', user['id']).execute()   # entries and reactions cascade
            cls.admin.table('profiles').delete().eq('id', user['id']).execute()
            cls.admin.auth.admin.delete_user(user['id'])
        created = [row['id'] for row in cls.admin.table('community_rounds').select('id').execute().data]
        for round_id in created:                      # the disposable database started without rounds
            cls.admin.table('community_rounds').delete().eq('id', round_id).execute()
        for motion, day in cls.featured_before.items():
            cls.admin.table('motions').update({'featured_on': day}).eq('id', motion).execute()
        cls.temp.cleanup()

    # helpers
    def uid(self, who):
        return self.users[who]['id']

    def record(self, who, round=True):
        data = {'motion_id': self.motion, 'side': 'yes'}
        if round:
            data['community_round_id'] = self.round
        response = self.client.post('/api/speeches', headers=self.users[who]['headers'], data=data,
                                    files={'audio': ('take.wav', self.audio, 'audio/wav')})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['speech_id']

    def share(self, speech_id, who):
        return self.client.post('/api/community/entries', headers=self.users[who]['headers'], json={'speech_id': speech_id})

    def board(self, who, **params):
        response = self.client.get('/api/community/leaderboard', headers=self.users[who]['headers'], params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def react(self, speech_id, value, who):
        return self.client.post(f'/api/community/speeches/{speech_id}/reaction', headers=self.users[who]['headers'],
                                json={'reaction': value})

    def stored(self, speech_id):
        return self.admin.table('speeches').select('*').eq('id', speech_id).execute().data[0]

    def refused(self, code, call):
        with self.assertRaises(APIError) as raised:
            call()
        self.assertEqual(raised.exception.code, code, raised.exception.message)

    def test_1_rounds_cannot_overlap_even_when_created_at_once(self):
        now = datetime.now(timezone.utc)
        self.assertIsNone(self.repository.create_round(self.motion, now, now + timedelta(minutes=5)))      # inside the open round
        self.refused('23514', lambda: self.admin.table('community_rounds').insert(
            dict(motion_id=self.motion, starts_at=(now + timedelta(days=30)).isoformat(), ends_at=(now + timedelta(days=29)).isoformat())).execute())
        start, end = now + timedelta(days=40), now + timedelta(days=41)
        with ThreadPoolExecutor(8) as pool:
            made = list(pool.map(lambda _: self.repository.create_round(self.motion, start, end), range(8)))
        self.assertEqual(len([round_id for round_id in made if round_id]), 1)
        # The same prompt on another day is a different round.
        current = self.repository.current_round()
        self.assertEqual((current['id'], current['motion_id'], bool(current['text'])), (self.round, self.motion, True))

    def test_2_complete_flow_with_real_constraints_and_concurrent_reactions(self):
        ada, grace, linus, mallory = (self.uid(name) for name in ('ada', 'grace', 'linus', 'mallory'))
        first, spare, theirs = self.record('ada'), self.record('ada'), self.record('grace')
        private = self.record('ada', round=False)
        row = self.stored(first)
        self.assertEqual((row['community_round_id'], row['moderation_status'], row['published']), (self.round, 'pending', False))
        ends = community.moment(self.repository.community_round(self.round)['ends_at'])
        self.assertEqual(community.moment(row['audio_expires_at']), ends + timedelta(minutes=5))
        self.assertEqual(self.repository.community_attempts(self.round, ada), 2)
        attempts = lambda who: self.client.get('/api/community/round', headers=self.users[who]['headers']).json()['attempts']
        self.assertEqual((attempts('ada'), attempts('grace'), attempts('linus')),
                         ({'used': 2, 'limit': 5}, {'used': 1, 'limit': 5}, {'used': 0, 'limit': 5}))   # private practice is not counted

        # Sharing: explicit, one speech per speaker per round, the speaker's own speech from this round only.
        self.assertEqual(self.share(private, 'ada').status_code, 409)
        self.assertEqual(self.share(first, 'grace').status_code, 404)
        self.assertEqual(self.share(self.record('mallory'), 'mallory').status_code, 409)          # no display name
        created = self.share(first, 'ada')
        self.assertEqual(created.status_code, 201, created.text)
        entry = created.json()['entry']
        self.assertEqual(self.share(first, 'ada').status_code, 200)
        self.assertEqual(self.share(spare, 'ada').status_code, 409)
        with self.assertRaises(EntryConflict):
            self.repository.create_entry(self.round, spare, ada)                                  # unique (round, player)
        insert = lambda **values: self.admin.table('community_entries').insert(values).execute()
        self.refused('23503', lambda: insert(round_id=self.round, speech_id=theirs, player_id=linus))    # someone else's speech
        self.refused('23503', lambda: insert(round_id=self.round, speech_id=private, player_id=linus))   # not recorded for a round
        self.assertEqual(self.share(theirs, 'grace').status_code, 201)
        self.assertTrue(self.stored(first)['published'])

        # Review gate: shared but pending speeches are invisible and silent.
        self.assertEqual(self.board('linus')['entries'], [])
        self.assertEqual(self.client.get(f'/api/community/speeches/{first}/audio', headers=self.users['linus']['headers']).status_code, 404)
        self.assertEqual(self.react(first, 'fire', 'linus').status_code, 404)
        self.assertEqual({item['speech_id'] for item in self.repository.pending_reviews()}, {first, theirs})
        for speech_id in (first, theirs):
            self.assertTrue(self.repository.review(speech_id, 'approved'))
        self.assertEqual(self.repository.pending_reviews(), [])
        self.admin.table('speeches').update({'overall': 61.5}).eq('id', theirs).execute()         # a clear grade order

        # Rankings come from SQL: grade order, absolute ranks across pages, zero counts when nobody reacted.
        board = self.board('linus', sort='grade')
        self.assertEqual([(e['rank'], e['speech_id'], e['overall']) for e in board['entries']],
                         [(1, first, SAMPLE['overall']['score']), (2, theirs, 61.5)])
        top = board['entries'][0]
        self.assertEqual((top['counts'], top['total_reactions'], top['my_reaction'], top['is_owner'], top['letter']),
                         ({'love': 0, 'like': 0, 'fire': 0, 'amazing': 0, 'downvote': 0}, 0, None, False, SAMPLE['overall']['letter']))
        self.assertEqual(set(top), {'id', 'speech_id', 'rank', 'display_name', 'side', 'overall', 'letter',
                                    'counts', 'total_reactions', 'reaction_score', 'my_reaction', 'is_owner'})
        self.assertEqual(top['id'], entry['id'])
        page = self.board('linus', sort='grade', limit=1, offset=1)
        self.assertEqual(([e['rank'] for e in page['entries']], page['has_more']), ([2], False))
        self.assertTrue(self.board('linus', limit=1)['has_more'])
        self.assertTrue(self.board('ada')['entries'][0]['is_owner'])

        # Reactions: one per account however many requests race; changing keeps the total; removal lowers it.
        emoji = ['love', 'like', 'fire', 'amazing', 'downvote'] * 3
        with ThreadPoolExecutor(12) as pool:
            results = list(pool.map(lambda value: self.repository.react(theirs, linus, value, 600), emoji))
        self.assertEqual({result['status'] for result in results}, {'ok'})
        rows = self.admin.table('speech_reactions').select('user_id,reaction').eq('speech_id', theirs).execute().data
        self.assertEqual([r['user_id'] for r in rows], [linus])
        liked = self.react(theirs, 'like', 'linus').json()
        self.assertEqual((liked['counts'], liked['total_reactions'], liked['my_reaction']),
                         ({'love': 0, 'like': 1, 'fire': 0, 'amazing': 0, 'downvote': 0}, 1, 'like'))
        both = self.react(theirs, 'fire', 'ada').json()
        self.assertEqual((both['counts'], both['total_reactions'], both['my_reaction']),
                         ({'love': 0, 'like': 1, 'fire': 1, 'amazing': 0, 'downvote': 0}, 2, 'fire'))
        self.assertEqual(self.react(theirs, 'amazing', 'ada').json()['total_reactions'], 2)       # changed, not added
        self.assertEqual(self.react(theirs, 'fire', 'mallory').json()['total_reactions'], 3)      # no name needed to react
        self.assertEqual(self.react(theirs, 'fire', 'grace').status_code, 403)                    # her own speech
        self.assertEqual(self.react(theirs, 'clap', 'linus').status_code, 422)
        self.refused('23514', lambda: self.admin.table('speech_reactions').insert(
            dict(speech_id=first, user_id=linus, reaction='clap')).execute())
        self.refused('23503', lambda: self.admin.table('speech_reactions').insert(
            dict(speech_id=private, user_id=linus, reaction='fire')).execute())                   # not a shared speech
        favorites = self.board('linus', sort='favorites')['entries']
        self.assertEqual([(e['rank'], e['speech_id'], e['total_reactions'], e['my_reaction']) for e in favorites],
                         [(1, theirs, 3, 'like'), (2, first, 0, None)])
        self.assertEqual(favorites[0]['counts'], {'love': 0, 'like': 1, 'fire': 1, 'amazing': 1, 'downvote': 0})
        removed = self.react(theirs, None, 'linus').json()
        self.assertEqual((removed['total_reactions'], removed['my_reaction']), (2, None))

        # Negative reactions persist once and lower favorites, without changing grade ranks.
        self.react(theirs, 'downvote', 'ada')
        negative = self.react(theirs, 'downvote', 'mallory').json()
        self.assertEqual((negative['total_reactions'], negative['reaction_score'], negative['counts']['downvote']), (2, -2, 2))
        self.assertEqual(self.react(theirs, 'downvote', 'mallory').json(), negative)
        self.assertEqual([e['speech_id'] for e in self.board('linus', sort='favorites')['entries']], [first, theirs])
        self.assertEqual(self.board('linus', sort='favorites', limit=1, offset=1)['entries'][0]['rank'], 2)
        self.assertEqual([e['speech_id'] for e in self.board('linus', sort='grade')['entries']], [first, theirs])
        self.assertEqual(self.react(theirs, 'downvote', 'grace').status_code, 403)
        self.react(theirs, None, 'ada')
        self.assertEqual(self.react(theirs, None, 'mallory').json()['reaction_score'], 0)
        self.react(theirs, 'amazing', 'ada')
        self.react(theirs, 'fire', 'mallory')

        # The per-account limit is counted in the database, across requests.
        statuses = [self.repository.react(first, linus, 'fire', 2)['status'] for _ in range(3)]
        self.assertEqual(statuses[-1], 'limited')
        self.admin.table('community_reaction_limits').delete().eq('user_id', linus).execute()

        # The public and account keys can neither call the functions nor touch the tables.
        guest = self.create_client(self.url, self.anon)
        account = self.create_client(self.url, self.anon)
        account.postgrest.auth(self.users['linus']['token'])
        for client in (guest, account):
            self.refused('42501', lambda: client.rpc('community_react', dict(p_speech=theirs, p_user=linus, p_reaction='love')).execute())
            self.refused('42501', lambda: client.rpc('community_board', dict(p_round=self.round, p_user=linus, p_sort='grade', p_limit=5, p_offset=0)).execute())
            self.refused('42501', lambda: client.rpc('community_audio', dict(p_speech=theirs)).execute())
            for table in ('community_rounds', 'community_entries', 'speech_reactions', 'community_reaction_limits'):
                try:                                  # RLS hides every row; a project without table grants refuses outright
                    self.assertEqual(client.table(table).select('*').execute().data, [], table)
                except APIError as error:
                    self.assertEqual(error.code, '42501', table)
            self.refused('42501', lambda: client.table('speech_reactions').insert(
                dict(speech_id=theirs, user_id=linus, reaction='love')).execute())
        self.assertEqual(self.react(theirs, None, 'ada').json()['total_reactions'], 1)             # untouched by those attempts

        # Playback: a private link on request, the real bytes, and nothing through the public path.
        audio = self.client.get(f'/api/community/speeches/{theirs}/audio', headers=self.users['linus']['headers']).json()
        self.assertEqual(httpx.get(audio['audio_url']).content, self.audio)
        self.assertLessEqual((community.moment(audio['expires_at']) - datetime.now(timezone.utc)).total_seconds(), 300)
        for hidden in (private, spare, str(uuid4())):
            self.assertEqual(self.client.get(f'/api/community/speeches/{hidden}/audio', headers=self.users['linus']['headers']).status_code, 404)

        # Withdrawal hides the speech at once; sharing the same speech again brings it back with its reactions.
        visibility = f"/api/community/entries/{self.repository.entry_for(self.round, grace)['id']}/visibility"
        self.assertEqual(self.client.post(visibility, headers=self.users['ada']['headers'], json={'shared': False}).status_code, 404)
        self.assertEqual(self.client.post(visibility, headers=self.users['grace']['headers'], json={'shared': False}).status_code, 200)
        self.assertEqual([e['speech_id'] for e in self.board('linus')['entries']], [first])
        self.assertEqual(self.client.get(f'/api/community/speeches/{theirs}/audio', headers=self.users['linus']['headers']).status_code, 404)
        self.assertEqual(self.react(theirs, 'love', 'linus').status_code, 404)
        self.assertFalse(self.stored(theirs)['published'])
        self.assertEqual(self.client.post(visibility, headers=self.users['grace']['headers'], json={'shared': True}).status_code, 200)
        self.assertEqual(self.board('linus', sort='favorites')['entries'][0]['total_reactions'], 1)
        mine = self.client.get('/api/community/round', headers=self.users['grace']['headers']).json()
        self.assertEqual((mine['round']['id'], mine['entry']['speech_id'], mine['entry']['withdrawn_at']), (self.round, theirs, None))

        # A rejected speech disappears and cannot be shared again.
        self.repository.review(first, 'rejected')
        self.assertEqual([e['speech_id'] for e in self.board('linus')['entries']], [theirs])
        self.assertEqual(self.share(first, 'ada').status_code, 409)
        card = self.client.get('/api/speeches/' + first, headers=self.users['ada']['headers']).json()
        self.assertEqual((card['moderation_status'], card['community_round_id']), ('rejected', self.round))

        # The round closes by its window alone: no board, no playback, no reactions, no new recordings.
        now = datetime.now(timezone.utc)
        self.admin.table('community_rounds').update({'starts_at': (now - timedelta(hours=3)).isoformat(),
                                                     'ends_at': (now - timedelta(minutes=10)).isoformat()}).eq('id', self.round).execute()
        closed = self.board('linus', round_id=self.round)
        self.assertEqual((closed['round']['id'], closed['entries']), (self.round, []))
        self.assertEqual(self.client.get('/api/community/round', headers=self.users['grace']['headers']).json()['round'], None)
        self.assertEqual(self.client.get(f'/api/community/speeches/{theirs}/audio', headers=self.users['linus']['headers']).status_code, 404)
        self.assertEqual(self.react(theirs, 'love', 'linus').status_code, 409)
        late = self.client.post('/api/speeches', headers=self.users['linus']['headers'], files={'audio': ('t.wav', self.audio, 'audio/wav')},
                                data={'motion_id': self.motion, 'side': 'no', 'community_round_id': self.round})
        self.assertEqual(late.status_code, 409)

        # Cleanup deletes the round's recordings from Storage and keeps every grade.
        tagged = [first, spare, theirs]
        self.admin.table('speeches').update({'audio_expires_at': (now - timedelta(minutes=5)).isoformat()}).eq('community_round_id', self.round).execute()
        path = self.stored(theirs)['audio_path']
        self.assertEqual(community.cleanup_audio(self.repository, dry_run=True)['expired'], 4)     # ada 2, grace 1, mallory 1
        self.assertEqual(community.cleanup_audio(self.repository), dict(expired=4, removed=4, failed=0))
        self.assertEqual(community.cleanup_audio(self.repository), dict(expired=0, removed=0, failed=0))
        for speech_id in tagged:
            after = self.stored(speech_id)
            self.assertEqual((after['audio_path'], after['published'], bool(after['audio_deleted_at'])), (None, False, True))
            self.assertEqual((after['status'], after['report']['overall']), ('done', SAMPLE['overall']))
        self.assertEqual(self.admin.storage.from_('audio').list(grace), [])
        self.assertGreaterEqual(httpx.get(f'{self.url}/storage/v1/object/authenticated/audio/{path}',
                                          headers={'Authorization': 'Bearer ' + self.users['grace']['token']}).status_code, 400)
        self.assertEqual(self.admin.table('community_entries').select('id').eq('round_id', self.round).execute().data, [])
        self.assertEqual(self.admin.table('speech_reactions').select('user_id').in_('speech_id', tagged).execute().data, [])
        kept = self.stored(private)                                                                # private practice is never touched
        self.assertEqual((bool(kept['audio_path']), kept['audio_deleted_at']), (True, None))
        self.assertEqual(len(self.admin.storage.from_('audio').list(ada)), 1)
        card = self.client.get('/api/speeches/' + theirs, headers=self.users['grace']['headers']).json()
        self.assertEqual((card['audio_url'], card['status'], card['report']['overall'], bool(card['audio_deleted_at'])),
                         (None, 'done', SAMPLE['overall'], True))
        progress = self.client.get('/api/me', headers=self.users['grace']['headers']).json()
        self.assertIn(theirs, [s['id'] for s in progress['speeches']])

    def test_3_rotation_creates_each_day_once_and_features_todays_prompt(self):
        plan = community.ensure_rounds(self.repository, days=3)
        self.assertEqual(len(plan), 3)
        again = community.ensure_rounds(self.repository, days=3)
        self.assertEqual(([item['created'] for item in again], [item['motion_id'] for item in again]),
                         ([False] * 3, [item['motion_id'] for item in plan]))
        current = self.repository.current_round()
        if current:                                   # absent only if the closed test round still covers this instant
            today = datetime.now(timezone.utc).date()
            self.assertEqual(self.repository.motion_featured_on(today), current['motion_id'])
            self.assertTrue(next(p for p in self.repository.prompts() if p['id'] == current['motion_id'])['featured'])
        # A second motion taking the same date clears the first: featured_on stays unique.
        other = next(p['id'] for p in self.repository.prompts() if not current or p['id'] != current['motion_id'])
        day = datetime.now(timezone.utc).date() + timedelta(days=20)
        self.repository.feature_motion(self.motion, day)
        self.repository.feature_motion(other, day)
        self.assertEqual(self.repository.motion_featured_on(day), other)


if __name__ == '__main__':
    unittest.main()
