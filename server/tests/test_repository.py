from types import SimpleNamespace
import unittest
from server.app.repository import SupabaseRepository


class Query:
    def __init__(self, trace):
        self.trace = trace
    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.trace.append((name, args, kwargs))
            if name == 'execute':
                return SimpleNamespace(data=[])
            return self
        return call


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.trace = []
        self.repo = object.__new__(SupabaseRepository)
        def get_user(token):
            self.trace.append(('get_user', token))
            return SimpleNamespace(user=SimpleNamespace(id='resolved-user'))
        self.repo.client = SimpleNamespace(auth=SimpleNamespace(get_user=get_user), table=lambda name: Query(self.trace))

    def test_auth_uses_remote_get_user_and_not_client_claims(self):
        self.assertEqual(self.repo.resolve_token('opaque.jwt'), 'resolved-user')
        self.assertEqual(self.trace, [('get_user', 'opaque.jwt')])

    def test_anonymous_token_cannot_access_account_api(self):
        self.repo.client.auth.get_user = lambda token: SimpleNamespace(user=SimpleNamespace(id='guest', is_anonymous=True))
        with self.assertRaises(ValueError):
            self.repo.resolve_token('valid-anonymous.jwt')

    def test_both_speech_reads_scope_player_id(self):
        self.repo.speech('some-id', 'resolved-user')
        self.assertIn(('eq', ('player_id', 'resolved-user'), {}), self.trace)
        self.trace.clear()
        self.repo.speeches('resolved-user')
        self.assertIn(('eq', ('player_id', 'resolved-user'), {}), self.trace)

    def test_private_url_short_lived_in_private_audio_bucket(self):
        def bucket(name):
            self.trace.append(('bucket', name))
            def sign(path, expires):
                self.trace.append(('signed', path, expires))
                return {'signedURL': 'signed'}
            return SimpleNamespace(create_signed_url=sign)
        self.repo.client.storage = SimpleNamespace(from_=bucket)
        self.assertEqual(self.repo.signed_audio('resolved-user/take.wav'), 'signed')
        self.assertEqual(self.trace, [('bucket', 'audio'), ('signed', 'resolved-user/take.wav', 300)])

    def test_unlimited_history_pages_beyond_supabase_row_limit(self):
        rows = [{'id': str(i), 'created_at': str(i), 'player_id': 'resolved-user'} for i in range(1205)]
        trace = self.trace
        class PageQuery(Query):
            def __init__(self):
                super().__init__(trace)
                self.start, self.end = 0, 999
            def range(self, start, end):
                trace.append(('range', start, end))
                self.start, self.end = start, end
                return self
            def execute(self):
                return SimpleNamespace(data=rows[self.start:self.end+1])
        self.repo.client.table = lambda name: PageQuery()
        result = self.repo.speeches('resolved-user')
        self.assertEqual(result, rows)
        self.assertGreater(len([item for item in trace if item[0] == 'range']), 1)
        self.assertEqual(len([item for item in trace if item[0] == 'eq' and item[1] == ('player_id', 'resolved-user')]),
                         len([item for item in trace if item[0] == 'range']))

    def test_board_reads_scores_only_and_skips_unnamed_players(self):
        tables = {'profiles': [{'id': 'a', 'display_name': 'Ada_1'}],
                  'speeches': [{'id': '1', 'player_id': 'a', 'overall': 82.5, 'letter': 'B', 'created_at': 't1'},
                               {'id': '2', 'player_id': 'unnamed', 'overall': 99, 'letter': 'A+', 'created_at': 't2'},
                               {'id': '3', 'player_id': 'a', 'overall': None, 'letter': None, 'created_at': 't3'}]}
        trace = self.trace
        class TableQuery(Query):
            def __init__(self, name):
                super().__init__(trace)
                self.name = name
            def execute(self):
                return SimpleNamespace(data=tables[self.name])
        self.repo.client.table = TableQuery
        self.assertEqual(self.repo.board_rows(), [dict(player_id='a', display_name='Ada_1', score=82.5, letter='B', created_at='t1')])
        self.assertIn(('neq', ('display_name', 'player'), {}), trace)
        selected = [item[1][0] for item in trace if item[0] == 'select']
        self.assertEqual(selected, ['id,display_name', 'id,player_id,overall,created_at,letter:report->overall->>letter'])

    def test_taken_name_is_refused_before_writing(self):
        from server.app.leaderboard import NameTaken
        trace = self.trace
        class Taken(Query):
            def execute(self):
                return SimpleNamespace(data=[{'id': 'someone-else'}])
        self.repo.client.table = lambda name: Taken(trace)
        with self.assertRaises(NameTaken):
            self.repo.set_display_name('resolved-user', 'Ada_1')
        self.assertIn(('ilike', ('display_name', 'Ada\\_1'), {}), trace)
        self.assertIn(('neq', ('id', 'resolved-user'), {}), trace)
        self.assertFalse([item for item in trace if item[0] == 'upsert'])


if __name__ == '__main__':
    unittest.main()
