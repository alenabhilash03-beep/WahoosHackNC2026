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


if __name__ == '__main__':
    unittest.main()
