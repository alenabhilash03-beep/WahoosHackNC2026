"""Community rounds, sharing, playback and reactions through the API, with an in-memory store.

The store below mirrors the rules of migration 0006 so the routes can be exercised without a
database. The rules themselves (constraints, the eligibility function, concurrent reactions)
are proven against real Postgres in test_supabase_integration.py and tools/database/.
"""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from server.app import community
from server.app.community import EntryConflict
from server.app.main import create_app
from server.app.session import InvalidSession
from server.app.settings import Settings
from server.scripts import cleanup_community_audio, manage_community_rounds, review_community
from test_api import Repository
from test_feedback import SAMPLE

USERS = {name: str(uuid4()) for name in ('ada', 'grace', 'linus')}
AUDIO = b'RIFFcommunity-take'


def iso(value):
    return value.isoformat()


class CommunityRepository(Repository):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.names = {USERS['ada']: 'Ada_1', USERS['grace']: 'Grace'}
        self.motions = [dict(id=str(uuid4()), text=text, category=None, featured_on=None)
                        for text in ('Should cities ban cars?', 'Should homework end?', 'Should voting be required?')]
        self.rounds, self.entries, self.reactions, self.requests = [], [], {}, {}
        self.removed, self.fail_remove = [], False

    # accounts and prompts
    def resolve_token(self, token):
        if token not in USERS:
            raise InvalidSession('bad')
        return USERS[token]

    def prompts(self):
        return [dict(id=m['id'], text=m['text'], featured=False) for m in self.motions]

    def create_speech(self, row):
        self.rows.append(dict(dict(moderation_status='pending', published=False, community_round_id=None,
                                   audio_expires_at=None, audio_deleted_at=None), **row,
                              created_at=iso(datetime.now(timezone.utc))))

    def signed_audio(self, path, seconds=300):
        self.signed.append((path, seconds))
        return f'https://private.example/{path}?seconds={seconds}'

    def remove_audio(self, path):
        if self.fail_remove:
            raise RuntimeError('storage down')
        self.removed.append(path)

    # rounds
    def _round(self, row):
        text = next(m['text'] for m in self.motions if m['id'] == row['motion_id'])
        return dict(row, text=text)

    def current_round(self, now=None):
        now = now or datetime.now(timezone.utc)
        return next((self._round(r) for r in self.rounds if community.is_open(r, now)), None)

    def community_round(self, id):
        return next((self._round(r) for r in self.rounds if r['id'] == id), None)

    def rounds_from(self, start):
        return sorted((self._round(r) for r in self.rounds if community.moment(r['ends_at']) > start), key=lambda r: r['starts_at'])

    def create_round(self, motion_id, starts_at, ends_at):
        if any(community.moment(r['starts_at']) < ends_at and community.moment(r['ends_at']) > starts_at for r in self.rounds):
            return None
        self.rounds.append(dict(id=str(uuid4()), motion_id=motion_id, starts_at=iso(starts_at), ends_at=iso(ends_at)))
        return self.rounds[-1]['id']

    def curated_motions(self):
        return sorted((dict(id=m['id'], text=m['text']) for m in self.motions if m['category'] is None), key=lambda m: m['text'])

    def motion_featured_on(self, day):
        return next((m['id'] for m in self.motions if m['featured_on'] == day.isoformat()), None)

    def feature_motion(self, motion_id, day):
        for m in self.motions:
            if m['featured_on'] == day.isoformat():
                m['featured_on'] = None
        next(m for m in self.motions if m['id'] == motion_id)['featured_on'] = day.isoformat()

    def community_attempts(self, round_id, user):
        return len([r for r in self.rows if r['community_round_id'] == round_id and r['player_id'] == user])

    # entries
    def entry_for(self, round_id, user):
        return next((dict(e) for e in self.entries if e['round_id'] == round_id and e['player_id'] == user), None)

    def entry(self, id, user):
        return next((dict(e) for e in self.entries if e['id'] == id and e['player_id'] == user), None)

    def create_entry(self, round_id, speech_id, user):
        if any((e['round_id'] == round_id and e['player_id'] == user) or e['speech_id'] == speech_id for e in self.entries):
            raise EntryConflict()
        self.entries.append(dict(id=str(uuid4()), round_id=round_id, speech_id=speech_id, player_id=user,
                                 shared_at=iso(datetime.now(timezone.utc)), withdrawn_at=None))
        return dict(self.entries[-1])

    def set_entry_visibility(self, id, user, shared):
        entry = next((e for e in self.entries if e['id'] == id and e['player_id'] == user), None)
        if entry:
            entry['withdrawn_at'] = None if shared else iso(datetime.now(timezone.utc))
        return dict(entry) if entry else None

    def set_published(self, speech_id, user, published):
        next(r for r in self.rows if r['id'] == speech_id and r['player_id'] == user)['published'] = published

    def review(self, speech_id, status):
        row = next((r for r in self.rows if r['id'] == speech_id), None)
        if row:
            row['moderation_status'] = status
            if status == 'rejected':
                row['published'] = False
        return bool(row)

    def pending_reviews(self):
        speeches = {r['id']: r for r in self.rows}
        return [dict(e, display_name=self.names.get(e['player_id']), audio_path=speeches[e['speech_id']]['audio_path'],
                     overall=speeches[e['speech_id']].get('overall'), side=speeches[e['speech_id']]['side'],
                     status=speeches[e['speech_id']]['status'])
                for e in self.entries if not e['withdrawn_at'] and speeches[e['speech_id']]['moderation_status'] == 'pending']

    # the eligibility rule of community_eligible_entries()
    def _eligible(self, round_id):
        now, speeches = datetime.now(timezone.utc), {r['id']: r for r in self.rows}
        round = next((r for r in self.rounds if r['id'] == round_id), None)
        if not community.is_open(round, now):
            return []
        return [(e, speeches[e['speech_id']]) for e in self.entries
                if e['round_id'] == round_id and not e['withdrawn_at']
                and speeches[e['speech_id']]['status'] == 'done' and speeches[e['speech_id']].get('overall') is not None
                and speeches[e['speech_id']]['moderation_status'] == 'approved'
                and community.audio_available(speeches[e['speech_id']], now)]

    def _counts(self, speech_id, user):
        votes = self.reactions.get(speech_id, {})
        return dict(counts={name: list(votes.values()).count(name) for name in community.REACTIONS},
                    total_reactions=len(votes), my_reaction=votes.get(user))

    def community_board(self, round_id, user, sort, limit, offset):
        rows = [dict(id=e['id'], speech_id=e['speech_id'], display_name=self.names[e['player_id']], side=s['side'],
                     overall=s['overall'], letter=s['report']['overall']['letter'], is_owner=e['player_id'] == user,
                     shared_at=e['shared_at'], **self._counts(e['speech_id'], user)) for e, s in self._eligible(round_id)]
        rows.sort(key=lambda r: (-(r['total_reactions'] if sort == 'favorites' else 0), -r['overall'], r['shared_at'], r['id']))
        for place, row in enumerate(rows, 1):
            row['rank'] = place
        return rows[offset:offset + limit]

    def community_audio(self, speech_id):
        for round in self.rounds:
            for entry, speech in self._eligible(round['id']):
                if entry['speech_id'] == speech_id:
                    return dict(audio_path=speech['audio_path'], ends_at=round['ends_at'])
        return None

    def react(self, speech_id, user, reaction, per_minute):
        entry = next((e for e in self.entries if e['speech_id'] == speech_id), None)
        if entry is None:
            return {'status': 'not_found'}
        round = next(r for r in self.rounds if r['id'] == entry['round_id'])
        if datetime.now(timezone.utc) >= community.moment(round['ends_at']):
            return {'status': 'closed'}
        if not any(e['speech_id'] == speech_id for e, _ in self._eligible(round['id'])):
            return {'status': 'not_found'}
        if entry['player_id'] == user:
            return {'status': 'own'}
        self.requests[user] = self.requests.get(user, 0) + 1
        if self.requests[user] > per_minute:
            return {'status': 'limited', 'retry_after': 17}
        votes = self.reactions.setdefault(speech_id, {})
        if reaction is None:
            votes.pop(user, None)
        else:
            votes[user] = reaction
        return dict(status='ok', **self._counts(speech_id, user))

    # cleanup
    def expired_community_audio(self, now, limit):
        return [dict(id=r['id'], audio_path=r['audio_path']) for r in self.rows
                if r['community_round_id'] and r['audio_path'] and community.moment(r['audio_expires_at']) < now][:limit]

    def finish_audio_cleanup(self, speech_id, path, now):
        self.entries = [e for e in self.entries if e['speech_id'] != speech_id]
        self.reactions.pop(speech_id, None)
        next(r for r in self.rows if r['id'] == speech_id).update(audio_path=None, audio_deleted_at=iso(now), published=False)


class CommunityTests(unittest.TestCase):
    def setUp(self):
        self.repo = CommunityRepository()
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_dir=Path(self.temp.name), community_enabled=True)
        self.client = TestClient(create_app(self.settings, self.repo, lambda *args: copy.deepcopy(SAMPLE)))
        now = datetime.now(timezone.utc)
        self.motion = self.repo.motions[0]['id']
        self.round = self.repo.create_round(self.motion, now - timedelta(hours=1), now + timedelta(hours=1))

    def tearDown(self):
        self.temp.cleanup()

    def auth(self, who):
        return {'Authorization': 'Bearer ' + who}

    def record(self, who='ada', round=True, motion=None, audio=AUDIO):
        data = {'motion_id': motion or self.motion, 'side': 'yes'}
        if round:
            data['community_round_id'] = self.round if round is True else round
        return self.client.post('/api/speeches', headers=self.auth(who), data=data, files={'audio': ('take.wav', audio, 'audio/wav')})

    def speech(self, who='ada', **kwargs):
        response = self.record(who, **kwargs)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['speech_id']

    def share(self, speech_id, who='ada'):
        return self.client.post('/api/community/entries', headers=self.auth(who), json={'speech_id': speech_id})

    def shared(self, who='ada', approve=True):
        speech_id = self.speech(who)
        response = self.share(speech_id, who)
        self.assertEqual(response.status_code, 201, response.text)
        if approve:
            self.repo.review(speech_id, 'approved')
        return speech_id, response.json()['entry']['id']

    def board(self, who='grace', **params):
        return self.client.get('/api/community/leaderboard', headers=self.auth(who), params=params)

    def react(self, speech_id, value, who='grace'):
        return self.client.post(f'/api/community/speeches/{speech_id}/reaction', headers=self.auth(who), json={'reaction': value})

    def close_round(self):
        now = datetime.now(timezone.utc)
        self.repo.rounds[0].update(starts_at=iso(now - timedelta(hours=2)), ends_at=iso(now - timedelta(minutes=1)))

    # availability
    def test_community_is_off_unless_the_server_enables_it(self):
        routes = [('get', '/api/community/round'), ('get', '/api/community/leaderboard'),
                  ('post', '/api/community/entries'), ('post', f'/api/community/entries/{uuid4()}/visibility'),
                  ('get', f'/api/community/speeches/{uuid4()}/audio'), ('post', f'/api/community/speeches/{uuid4()}/reaction')]
        off = TestClient(create_app(Settings(data_dir=Path(self.temp.name)), self.repo))
        local = TestClient(create_app(Settings(data_dir=Path(self.temp.name), local_mode=True, community_enabled=True)),
                           base_url='http://localhost', client=('127.0.0.1', 1234))
        for method, path in routes:
            self.assertEqual(getattr(off, method)(path, headers=self.auth('ada')).status_code, 503, path)
            self.assertEqual(getattr(local, method)(path, headers={'Authorization': 'Bearer local-player'}).status_code, 503, path)
            self.assertEqual(getattr(self.client, method)(path).status_code, 401, path)
        self.assertIn('"COMMUNITY_ENABLED": false', off.get('/config.js').text)
        self.assertIn('"COMMUNITY_ENABLED": false', local.get('/config.js').text)
        self.assertIn('"COMMUNITY_ENABLED": true', self.client.get('/config.js').text)
        # A recording tagged for a round is refused, not silently stored as private practice.
        tagged = off.post('/api/speeches', headers=self.auth('ada'), files={'audio': ('a.wav', AUDIO, 'audio/wav')},
                          data={'motion_id': self.motion, 'side': 'yes', 'community_round_id': self.round})
        self.assertEqual((tagged.status_code, self.repo.uploads), (503, []))

    def test_round_shows_the_open_prompt_the_callers_entry_and_name(self):
        data = self.client.get('/api/community/round', headers=self.auth('ada')).json()
        self.assertEqual(data['round']['id'], self.round)
        self.assertEqual(data['round']['prompt'], {'id': self.motion, 'text': 'Should cities ban cars?'})
        self.assertEqual((data['entry'], data['display_name']), (None, 'Ada_1'))
        self.assertEqual(data['attempts'], {'used': 0, 'limit': 5})
        speech_id, entry_id = self.shared()
        self.speech(round=False)                                             # private practice does not use a recording
        self.assertEqual(self.client.get('/api/community/round', headers=self.auth('ada')).json()['attempts'], {'used': 1, 'limit': 5})
        self.assertEqual(self.client.get('/api/community/round', headers=self.auth('grace')).json()['attempts'], {'used': 0, 'limit': 5})
        mine = self.client.get('/api/community/round', headers=self.auth('ada')).json()['entry']
        self.assertEqual(mine, {'id': entry_id, 'speech_id': speech_id, 'withdrawn_at': None})
        other = self.client.get('/api/community/round', headers=self.auth('linus')).json()
        self.assertEqual((other['entry'], other['display_name']), (None, None))
        self.close_round()
        self.assertEqual(self.client.get('/api/community/round', headers=self.auth('ada')).json(),
                         {'round': None, 'entry': None, 'display_name': 'Ada_1', 'attempts': None})

    # recording for a round
    def test_round_recording_is_tagged_and_expires_five_minutes_after_the_round(self):
        speech_id = self.speech()
        row = next(r for r in self.repo.rows if r['id'] == speech_id)
        ends = community.moment(self.repo.rounds[0]['ends_at'])
        self.assertEqual(row['community_round_id'], self.round)
        self.assertEqual(community.moment(row['audio_expires_at']), ends + timedelta(minutes=5))
        self.assertEqual((row['published'], row['moderation_status']), (False, 'pending'))
        private_id = self.speech(round=False)
        private = next(r for r in self.repo.rows if r['id'] == private_id)
        self.assertEqual((private['community_round_id'], private['audio_expires_at']), (None, None))

    def test_round_recording_is_refused_before_anything_is_stored(self):
        other_motion = self.repo.motions[1]['id']
        self.assertEqual(self.record(motion=other_motion).status_code, 422)
        self.assertEqual(self.record(round=str(uuid4())).status_code, 404)
        self.assertEqual(self.record(round='not-a-uuid').status_code, 404)
        self.settings.community_max_audio_bytes = 8
        self.assertEqual(self.record().status_code, 413)
        self.settings.community_max_audio_bytes = 1024
        self.close_round()
        self.assertEqual(self.record().status_code, 409)
        self.assertEqual((self.repo.uploads, self.repo.rows), ([], []))

    def test_recordings_per_round_are_capped_per_account(self):
        self.assertEqual(Settings().community_attempts_per_round, 5)        # the default: five recordings of the Daily Prompt
        self.settings.community_attempts_per_round = 2
        self.speech(); self.speech()
        refused = self.record()
        self.assertEqual(refused.status_code, 429)
        self.assertIn('all 2 recordings', refused.json()['detail'])
        self.assertEqual(len(self.repo.uploads), 2)                          # the sixth take is refused before it is stored
        self.assertEqual(self.client.get('/api/community/round', headers=self.auth('ada')).json()['attempts'], {'used': 2, 'limit': 2})
        self.assertEqual(self.record('grace').status_code, 202)          # the cap is per account
        self.assertEqual(self.record(round=False).status_code, 202)      # private practice is not capped

    # sharing
    def test_sharing_is_explicit_reviewed_and_one_speech_per_round(self):
        first, second = self.speech(), self.speech()
        self.assertEqual(self.board().json()['entries'], [])             # graded, but never shared
        created = self.share(first)
        self.assertEqual(created.status_code, 201)
        entry = created.json()['entry']
        self.assertEqual((entry['speech_id'], entry['withdrawn_at']), (first, None))
        self.assertEqual(self.board().json()['entries'], [])             # shared, waiting for review
        self.assertEqual(self.client.get(f'/api/community/speeches/{first}/audio', headers=self.auth('grace')).status_code, 404)
        self.assertEqual(self.share(first).status_code, 200)             # the same share again changes nothing
        self.assertEqual(len(self.repo.entries), 1)
        self.assertEqual(self.share(second).status_code, 409)            # never a different speech
        self.repo.review(first, 'approved')
        self.assertEqual([e['speech_id'] for e in self.board().json()['entries']], [first])
        self.repo.review(first, 'rejected')
        self.assertEqual(self.board().json()['entries'], [])
        self.assertEqual(self.share(first).status_code, 409)

    def test_only_an_owned_graded_named_round_speech_can_be_shared(self):
        speech_id = self.speech()
        self.assertEqual(self.share(speech_id, 'grace').status_code, 404)        # not hers
        self.assertEqual(self.share(str(uuid4())).status_code, 404)
        self.assertEqual(self.share('not-a-uuid').status_code, 404)
        self.assertEqual(self.share(self.speech(round=False)).status_code, 409)  # private practice
        self.assertEqual(self.share(self.speech('linus'), 'linus').status_code, 409)   # no display name
        row = next(r for r in self.repo.rows if r['id'] == speech_id)
        for change in (dict(status='ungradable'), dict(status='failed'), dict(status='done', audio_path=None),
                       dict(audio_path='x.wav', audio_expires_at=iso(datetime.now(timezone.utc) - timedelta(seconds=1)))):
            saved = dict(row)
            row.update(change)
            self.assertEqual(self.share(speech_id).status_code, 409, change)
            row.clear(); row.update(saved)
        self.close_round()
        self.assertEqual(self.share(speech_id).status_code, 409)
        self.assertEqual(self.repo.entries, [])

    def test_a_simultaneous_second_share_is_settled_by_the_database(self):
        first, second = self.speech(), self.speech()
        original, calls = self.repo.entry_for, []

        def stale(round_id, user):
            calls.append(1)
            return None if len(calls) == 1 else original(round_id, user)   # the first look predates the other tab's insert

        self.repo.create_entry(self.round, first, USERS['ada'])
        self.repo.entry_for = stale
        self.assertEqual(self.share(second).status_code, 409)
        calls.clear()
        self.assertEqual(self.share(first).status_code, 200)
        self.assertEqual(len(self.repo.entries), 1)

    def test_auto_approve_is_an_explicit_server_setting(self):
        self.settings.community_auto_approve = True
        speech_id, _ = self.shared(approve=False)
        self.assertEqual([e['speech_id'] for e in self.board().json()['entries']], [speech_id])
        self.repo.review(speech_id, 'rejected')
        self.assertEqual(self.share(speech_id).status_code, 409)             # never overrides a rejection
        self.assertEqual(self.board().json()['entries'], [])

    def test_withdrawing_hides_the_speech_and_the_same_one_can_be_shared_again(self):
        speech_id, entry_id = self.shared()
        self.react(speech_id, 'fire')
        url = f'/api/community/entries/{entry_id}/visibility'
        self.assertEqual(self.client.post(url, headers=self.auth('grace'), json={'shared': False}).status_code, 404)
        self.assertEqual(self.client.post(f'/api/community/entries/{uuid4()}/visibility', headers=self.auth('ada'), json={'shared': False}).status_code, 404)
        withdrawn = self.client.post(url, headers=self.auth('ada'), json={'shared': False})
        self.assertEqual(withdrawn.status_code, 200)
        self.assertIsNotNone(withdrawn.json()['entry']['withdrawn_at'])
        row = next(r for r in self.repo.rows if r['id'] == speech_id)
        self.assertFalse(row['published'])
        self.assertEqual(self.board().json()['entries'], [])
        self.assertEqual(self.client.get(f'/api/community/speeches/{speech_id}/audio', headers=self.auth('grace')).status_code, 404)
        self.assertEqual(self.react(speech_id, 'love').status_code, 404)
        again = self.client.post(url, headers=self.auth('ada'), json={'shared': True})
        self.assertEqual((again.status_code, again.json()['entry']['withdrawn_at'], row['published']), (200, None, True))
        self.assertEqual(self.board().json()['entries'][0]['total_reactions'], 1)   # earlier reactions were kept
        self.close_round()
        self.assertEqual(self.client.post(url, headers=self.auth('ada'), json={'shared': True}).status_code, 409)
        self.assertEqual(self.client.post(url, headers=self.auth('ada'), json={'shared': False}).status_code, 200)   # leaving is always allowed

    # rankings
    def test_board_ranks_by_grade_or_reactions_and_reveals_nothing_private(self):
        low = copy.deepcopy(SAMPLE)
        low['overall'] = {'score': 61.5, 'letter': 'D-'}
        ada, _ = self.shared('ada')
        grace, _ = self.shared('grace')
        next(r for r in self.repo.rows if r['id'] == grace).update(overall=61.5, report=low)
        self.repo.names[USERS['linus']] = 'Linus'
        self.assertEqual(self.react(grace, 'love', 'linus').status_code, 200)
        self.assertEqual(self.react(grace, 'fire', 'ada').status_code, 200)
        by_grade = self.board('linus', sort='grade').json()
        self.assertEqual([(e['rank'], e['display_name'], e['overall'], e['letter']) for e in by_grade['entries']],
                         [(1, 'Ada_1', SAMPLE['overall']['score'], SAMPLE['overall']['letter']), (2, 'Grace', 61.5, 'D-')])
        favorites = self.board('linus', sort='favorites').json()
        self.assertEqual([e['display_name'] for e in favorites['entries']], ['Grace', 'Ada_1'])
        top = favorites['entries'][0]
        self.assertEqual(top['counts'], {'love': 1, 'like': 0, 'fire': 1, 'amazing': 0})
        self.assertEqual((top['total_reactions'], top['my_reaction'], top['is_owner']), (2, 'love', False))
        self.assertEqual(set(top), {'id', 'speech_id', 'rank', 'display_name', 'side', 'overall', 'letter',
                                    'counts', 'total_reactions', 'my_reaction', 'is_owner'})
        mine = self.board('grace', sort='favorites').json()['entries'][0]
        self.assertEqual((mine['is_owner'], mine['my_reaction']), (True, None))
        text = self.board('linus').text
        for private in ('transcript', 'audio_path', USERS['ada'], USERS['grace'], 'evidence', 'player_id'):
            self.assertNotIn(private, text)
        self.assertEqual(favorites['round']['id'], self.round)

    def test_board_pages_keep_absolute_ranks(self):
        ada, _ = self.shared('ada')
        grace, _ = self.shared('grace')
        first = self.board(limit=1, offset=0).json()
        second = self.board(limit=1, offset=1).json()
        self.assertEqual((len(first['entries']), first['has_more'], first['entries'][0]['rank']), (1, True, 1))
        self.assertEqual((len(second['entries']), second['has_more'], second['entries'][0]['rank']), (1, False, 2))
        self.assertEqual(self.board(limit=1, offset=5).json()['entries'], [])
        for bad in (dict(sort='newest'), dict(limit=0), dict(limit=51), dict(offset=-1)):
            self.assertEqual(self.board(**bad).status_code, 422, bad)
        self.assertEqual(self.board(round_id=str(uuid4())).status_code, 404)
        self.assertEqual(self.board(round_id='x').status_code, 404)

    def test_a_closed_round_has_no_board_playback_or_reactions(self):
        speech_id, _ = self.shared()
        self.close_round()
        closed = self.board(round_id=self.round).json()
        self.assertEqual((closed['round']['id'], closed['entries'], closed['has_more']), (self.round, [], False))
        self.assertEqual(self.board().json(), {'round': None, 'entries': [], 'has_more': False})
        self.assertEqual(self.client.get(f'/api/community/speeches/{speech_id}/audio', headers=self.auth('grace')).status_code, 404)
        self.assertEqual(self.react(speech_id, 'fire').status_code, 409)

    # playback
    def test_playback_link_is_signed_on_demand_and_never_outlives_the_round(self):
        speech_id, _ = self.shared()
        self.assertEqual(self.repo.signed, [])                               # the board signs nothing
        self.board()
        self.assertEqual(self.repo.signed, [])
        data = self.client.get(f'/api/community/speeches/{speech_id}/audio', headers=self.auth('grace')).json()
        path, seconds = self.repo.signed[-1]
        self.assertTrue(path.startswith(USERS['ada'] + '/'))
        self.assertEqual(seconds, 300)
        self.assertLess(abs((community.moment(data['expires_at']) - datetime.now(timezone.utc)).total_seconds() - 300), 5)
        self.repo.rounds[0]['ends_at'] = iso(datetime.now(timezone.utc) + timedelta(seconds=40))
        self.client.get(f'/api/community/speeches/{speech_id}/audio', headers=self.auth('grace'))
        self.assertTrue(30 <= self.repo.signed[-1][1] <= 40)
        for missing in (str(uuid4()), 'not-a-uuid', self.speech('grace')):  # unknown, malformed, never shared
            self.assertEqual(self.client.get(f'/api/community/speeches/{missing}/audio', headers=self.auth('grace')).status_code, 404)

    # reactions
    def test_one_reaction_per_account_can_be_changed_and_removed(self):
        speech_id, _ = self.shared()
        first = self.react(speech_id, 'fire')
        self.assertEqual(first.json(), {'counts': {'love': 0, 'like': 0, 'fire': 1, 'amazing': 0}, 'total_reactions': 1, 'my_reaction': 'fire'})
        self.assertEqual(self.react(speech_id, 'fire').json()['total_reactions'], 1)        # a repeat is still one vote
        changed = self.react(speech_id, 'amazing').json()
        self.assertEqual((changed['counts'], changed['total_reactions'], changed['my_reaction']),
                         ({'love': 0, 'like': 0, 'fire': 0, 'amazing': 1}, 1, 'amazing'))
        self.repo.names[USERS['linus']] = 'Linus'
        self.assertEqual(self.react(speech_id, 'like', 'linus').json()['total_reactions'], 2)
        removed = self.react(speech_id, None).json()
        self.assertEqual((removed['total_reactions'], removed['my_reaction'], removed['counts']['like']), (1, None, 1))
        self.assertEqual(self.react(speech_id, None).json()['total_reactions'], 1)          # removing twice is harmless

    def test_reaction_refusals(self):
        speech_id, _ = self.shared()
        self.assertEqual(self.react(speech_id, 'fire', 'ada').status_code, 403)             # your own speech
        for bad in ('clap', '', 'FIRE', 5):
            self.assertEqual(self.react(speech_id, bad).status_code, 422, bad)
        self.assertEqual(self.client.post(f'/api/community/speeches/{speech_id}/reaction', headers=self.auth('grace'), json={}).status_code, 422)
        self.assertEqual(self.react(str(uuid4()), 'fire').status_code, 404)
        self.assertEqual(self.react('not-a-uuid', 'fire').status_code, 404)
        pending, _ = self.shared('grace', approve=False)
        self.assertEqual(self.react(pending, 'fire', 'ada').status_code, 404)               # not approved yet
        self.assertEqual(self.repo.reactions, {})

    def test_reactions_are_rate_limited_per_account(self):
        speech_id, _ = self.shared()
        self.settings.community_reactions_per_minute = 3
        for value in ('fire', 'love', None):
            self.assertEqual(self.react(speech_id, value).status_code, 200)
        limited = self.react(speech_id, 'fire')
        self.assertEqual((limited.status_code, limited.headers['Retry-After']), (429, '17'))
        self.assertIn('17 seconds', limited.json()['detail'])
        self.assertEqual(self.repo.reactions[speech_id], {})

    # the owner's card
    def test_card_reports_community_state_and_survives_audio_expiry(self):
        speech_id = self.speech()
        card = self.client.get('/api/speeches/' + speech_id, headers=self.auth('ada')).json()
        self.assertEqual((card['community_round_id'], card['moderation_status'], card['audio_deleted_at']), (self.round, 'pending', None))
        self.assertTrue(card['audio_url'] and card['audio_expires_at'])
        row = next(r for r in self.repo.rows if r['id'] == speech_id)
        row['audio_expires_at'] = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
        expired = self.client.get('/api/speeches/' + speech_id, headers=self.auth('ada')).json()
        self.assertEqual((expired['audio_url'], expired['status'], expired['report']['overall']), (None, 'done', SAMPLE['overall']))
        private = self.client.get('/api/speeches/' + self.speech(round=False), headers=self.auth('ada')).json()
        self.assertEqual([private[key] for key in ('community_round_id', 'moderation_status', 'audio_expires_at', 'audio_deleted_at')], [None] * 4)
        self.assertTrue(private['audio_url'])


class UpkeepTests(unittest.TestCase):
    def setUp(self):
        self.repo = CommunityRepository()
        self.now = datetime(2026, 10, 11, 15, 30, tzinfo=timezone.utc)

    def test_rounds_are_created_once_per_day_and_align_the_daily_prompt(self):
        plan = community.ensure_rounds(self.repo, days=3, now=self.now)
        self.assertEqual([(item['day'], item['created']) for item in plan],
                         [('2026-10-11', True), ('2026-10-12', True), ('2026-10-13', True)])
        windows = [(r['starts_at'], r['ends_at']) for r in sorted(self.repo.rounds, key=lambda r: r['starts_at'])]
        self.assertEqual(windows[0], ('2026-10-11T00:00:00+00:00', '2026-10-12T00:00:00+00:00'))
        self.assertEqual(len({r['id'] for r in self.repo.rounds}), 3)
        today = self.repo.current_round(self.now)
        self.assertEqual(self.repo.motion_featured_on(self.now.date()), today['motion_id'])
        again = community.ensure_rounds(self.repo, days=3, now=self.now)
        self.assertEqual(([item['created'] for item in again], len(self.repo.rounds)), ([False] * 3, 3))
        self.assertEqual([item['motion_id'] for item in again], [item['motion_id'] for item in plan])

    def test_round_prompt_prefers_chosen_ids_then_the_daily_prompt_then_rotation(self):
        featured, chosen = self.repo.motions[2], self.repo.motions[1]
        featured['featured_on'] = '2026-10-11'
        plan = community.ensure_rounds(self.repo, days=1, now=self.now)
        self.assertEqual(plan[0]['motion_id'], featured['id'])                 # keeps today's Daily Prompt
        plan = community.ensure_rounds(self.repo, days=3, motions=[chosen['id']], now=self.now)
        self.assertEqual((plan[0]['created'], plan[1]['motion_id'], plan[1]['created']), (False, chosen['id'], True))
        pool = [m['id'] for m in self.repo.curated_motions()]
        self.assertIn(plan[2]['motion_id'], pool)
        # The same prompt on a later day is a new round with its own board.
        later = community.ensure_rounds(self.repo, days=8, now=self.now)
        by_motion = {}
        for item in later:
            by_motion.setdefault(item['motion_id'], []).append(item['day'])
        self.assertTrue(any(len(days) > 1 for days in by_motion.values()))
        self.assertEqual(len(self.repo.rounds), 8)

    def test_dry_run_and_scripts_change_nothing_without_apply(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(manage_community_rounds.main(['--days', '2'], self.repo), 0)
        self.assertEqual((self.repo.rounds, 'Dry run' in out.getvalue()), ([], True))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(manage_community_rounds.main(['--days', '2', '--apply'], self.repo), 0)
        self.assertEqual(len(self.repo.rounds), 2)

    def community_speech(self, expires, path='u/take.wav', round_id=None):
        row = dict(id=str(uuid4()), player_id=USERS['ada'], motion_id=self.repo.motions[0]['id'], side='yes', status='done',
                   overall=90, report=copy.deepcopy(SAMPLE), audio_path=path, community_round_id=round_id or str(uuid4()),
                   audio_expires_at=iso(expires), audio_deleted_at=None, published=True, moderation_status='approved',
                   created_at=iso(self.now))
        self.repo.rows.append(row)
        return row

    def test_cleanup_deletes_only_expired_community_recordings_and_keeps_grades(self):
        expired = self.community_speech(self.now - timedelta(minutes=1), 'u/old.wav')
        open_ = self.community_speech(self.now + timedelta(hours=1), 'u/open.wav')
        private = dict(self.community_speech(self.now - timedelta(days=9), 'u/private.wav'), community_round_id=None)
        self.repo.rows[-1] = private
        self.repo.entries.append(dict(id=str(uuid4()), round_id=expired['community_round_id'], speech_id=expired['id'],
                                      player_id=USERS['ada'], shared_at=iso(self.now), withdrawn_at=None))
        self.repo.reactions[expired['id']] = {USERS['grace']: 'fire'}
        self.assertEqual(community.cleanup_audio(self.repo, now=self.now, dry_run=True), dict(expired=1, removed=0, failed=0))
        self.assertEqual(self.repo.removed, [])
        self.assertEqual(community.cleanup_audio(self.repo, now=self.now), dict(expired=1, removed=1, failed=0))
        self.assertEqual(self.repo.removed, ['u/old.wav'])
        self.assertEqual((expired['audio_path'], expired['published'], bool(expired['audio_deleted_at'])), (None, False, True))
        self.assertEqual((expired['overall'], expired['report'], expired['status']), (90, SAMPLE, 'done'))   # the grade stays
        self.assertEqual((self.repo.entries, self.repo.reactions), ([], {}))
        self.assertEqual((open_['audio_path'], private['audio_path']), ('u/open.wav', 'u/private.wav'))
        self.assertEqual(community.cleanup_audio(self.repo, now=self.now), dict(expired=0, removed=0, failed=0))   # a second run is harmless

    def test_cleanup_keeps_the_path_when_storage_fails_and_finishes_next_time(self):
        row = self.community_speech(datetime(2020, 1, 1, tzinfo=timezone.utc))   # expired by any clock: the script uses the real one
        self.repo.fail_remove = True
        with self.assertLogs('server.app.community', level='ERROR'):
            self.assertEqual(community.cleanup_audio(self.repo, now=self.now), dict(expired=1, removed=0, failed=1))
        self.assertEqual((row['audio_path'], row['audio_deleted_at']), ('u/take.wav', None))
        with contextlib.redirect_stdout(io.StringIO()), self.assertLogs('server.app.community', level='ERROR'):
            self.assertEqual(cleanup_community_audio.main(['--apply'], self.repo), 1)    # the script reports the failure
        self.repo.fail_remove = False
        self.assertEqual(community.cleanup_audio(self.repo, now=self.now)['removed'], 1)
        self.assertIsNone(row['audio_path'])

    def test_review_script_lists_approves_and_rejects(self):
        round_id = self.repo.create_round(self.repo.motions[0]['id'], self.now - timedelta(hours=1), self.now + timedelta(hours=1))
        row = self.community_speech(self.now + timedelta(hours=2), round_id=round_id)
        row.update(moderation_status='pending')
        self.repo.entries.append(dict(id=str(uuid4()), round_id=round_id, speech_id=row['id'], player_id=USERS['ada'],
                                      shared_at=iso(self.now), withdrawn_at=None))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(review_community.main([], self.repo), 0)
        self.assertIn(row['id'], out.getvalue())
        self.assertIn('1 shared speech(es) waiting', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(review_community.main(['--approve', row['id']], self.repo), 0)
            self.assertEqual(row['moderation_status'], 'approved')
            self.assertEqual(review_community.main(['--reject', row['id']], self.repo), 0)
            self.assertEqual((row['moderation_status'], row['published']), ('rejected', False))
            self.assertEqual(review_community.main(['--approve', str(uuid4())], self.repo), 1)   # unknown id


if __name__ == '__main__':
    unittest.main()
