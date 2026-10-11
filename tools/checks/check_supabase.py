"""Read-only check of SpeakUp's current Supabase database and private storage.

Run: python tools/checks/check_supabase.py (reads server/.env and environment).
Optionally set SUPABASE_CHECK_ACCESS_TOKEN to an existing GitHub account's
Supabase token to check authenticated table isolation. Never prints credentials
or row data. Does not create users/write rows or require anonymous login/Realtime.
"""
import os
from pathlib import Path
import sys

from postgrest.exceptions import APIError
from supabase import create_client

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = {
    'profiles': 'id,display_name,created_at',
    'motions': 'id,text,active,featured_on,category,difficulty',
    'speeches': 'id,player_id,motion_id,side,audio_path,report,overall,standards_id,published,moderation_status,ranked,status,error,created_at',
    'judgments': 'id,rater_id,motion_id,speech_a,speech_b',
    'gold_pairs': 'speech_a,speech_b,expected_pick',
    'arena_scores': 'speech_id,logos,pathos,ethos,persuasion',
    'ratings': 'player_id,mode,rating,games',
}


def load_env():
    env = {}
    path = ROOT / 'server' / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                env[key.strip()] = value.strip().strip('\"\'')
    env.update({k: v for k, v in os.environ.items() if v})
    missing = [key for key in ('SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_ANON_KEY') if not env.get(key)]
    if missing:
        raise ValueError('Missing ' + ', '.join(missing))
    return env


def report(ok, label):
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    return ok


def browser_blocked(client, table):
    try:
        # SQL tests prove RLS on empty tables; an empty HTTP response alone cannot.
        return not client.table(table).select(COLUMNS[table].split(',')[0]).limit(1).execute().data
    except APIError as error:
        return error.code == '42501'


def main():
    try:
        env = load_env()
    except ValueError as error:
        print(error)
        return 1
    checks = []
    try:
        server = create_client(env['SUPABASE_URL'], env['SUPABASE_SERVICE_ROLE_KEY'])
        browser = create_client(env['SUPABASE_URL'], env['SUPABASE_ANON_KEY'])
        for table, columns in COLUMNS.items():
            try:
                server.table(table).select(columns).limit(1).execute()
                checks.append(report(True, f'server can query {table} columns'))
            except Exception:
                checks.append(report(False, f'server cannot query {table}; check migrations and server key'))
            checks.append(report(browser_blocked(browser, table), f'public key cannot read {table}'))
        checks.append(report(bool(server.table('motions').select('id').eq('active', True).limit(1).execute().data),
                             'at least one active prompt exists'))
        bucket = server.storage.get_bucket('audio')
        checks.append(report(bucket.public is False, 'audio bucket is private'))
        token = env.get('SUPABASE_CHECK_ACCESS_TOKEN')
        if token:
            account = server.auth.get_user(token).user
            valid = bool(account and not getattr(account, 'is_anonymous', False))
            checks.append(report(valid, 'existing account token resolves to a non-anonymous user'))
            if valid:
                browser.postgrest.auth(token)
                for table in COLUMNS:
                    checks.append(report(browser_blocked(browser, table), f'account cannot directly read {table}'))
        else:
            print('SKIP  account token not supplied; GitHub login and account isolation need a separate check')
    except Exception as error:
        # Provider exception messages can contain keys or token details.
        checks.append(report(False, f'connection/auth/storage check failed ({type(error).__name__})'))
    print('DATABASE CHECKS PASSED' if all(checks) else 'DATABASE CHECKS FAILED')
    return 0 if checks and all(checks) else 1


if __name__ == '__main__':
    sys.exit(main())
