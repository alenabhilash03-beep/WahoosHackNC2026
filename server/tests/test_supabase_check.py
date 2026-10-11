"""The live database probe must be read-only, fail closed and redact provider errors."""
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('supabase_check', ROOT / 'tools/checks/check_supabase.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class Query:
    # Deliberately has no insert/update/delete/upsert methods.
    def __init__(self, rows):
        self.rows = rows

    def select(self, *args):
        return self

    def limit(self, *args):
        return self

    def eq(self, *args):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class SupabaseCheckTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(SUPABASE_URL='http://localhost:54321', SUPABASE_SERVICE_ROLE_KEY='server-secret',
                        SUPABASE_ANON_KEY='public-key')
        self.server = SimpleNamespace(table=lambda name: Query([{'id': 'private-data'}]),
            storage=SimpleNamespace(get_bucket=lambda name: SimpleNamespace(public=False)),
            auth=SimpleNamespace(get_user=lambda token: SimpleNamespace(user=SimpleNamespace(is_anonymous=False))))
        self.browser = SimpleNamespace(table=lambda name: Query([]), postgrest=SimpleNamespace(auth=lambda token: None))

    def run_check(self):
        out = io.StringIO()
        with patch.object(check, 'load_env', return_value=self.env), \
                patch.object(check, 'create_client', side_effect=[self.server, self.browser]), redirect_stdout(out):
            result = check.main()
        return result, out.getvalue()

    def test_healthy_database_uses_only_read_operations(self):
        result, out = self.run_check()
        self.assertEqual(result, 0)
        self.assertIn('SKIP  account token not supplied', out)
        self.assertNotIn('private-data', out)
        self.assertNotIn('server-secret', out)

    def test_visible_browser_rows_fail_the_check(self):
        self.browser.table = lambda name: Query([{'id': 'leaked'}])
        result, out = self.run_check()
        self.assertEqual(result, 1)
        self.assertIn('FAIL  public key cannot read speeches', out)
        self.assertNotIn('leaked', out)

    def test_missing_schema_fails_without_printing_provider_details(self):
        def broken(name):
            raise RuntimeError('server-secret in provider request URL')
        self.server.table = broken
        result, out = self.run_check()
        self.assertEqual(result, 1)
        self.assertIn('server cannot query profiles', out)
        self.assertNotIn('server-secret', out)

    def test_anonymous_token_cannot_pass_account_acceptance(self):
        self.env['SUPABASE_CHECK_ACCESS_TOKEN'] = 'account-token'
        self.server.auth.get_user = lambda token: SimpleNamespace(user=SimpleNamespace(is_anonymous=True))
        result, out = self.run_check()
        self.assertEqual(result, 1)
        self.assertIn('FAIL  existing account token', out)
        self.assertNotIn('account-token', out)

    def test_public_audio_bucket_fails(self):
        self.server.storage.get_bucket = lambda name: SimpleNamespace(public=True)
        result, out = self.run_check()
        self.assertEqual(result, 1)
        self.assertIn('FAIL  audio bucket is private', out)


if __name__ == '__main__':
    unittest.main()
