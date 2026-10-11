"""Service-role operations. Every speech query explicitly scopes its owner."""
from datetime import datetime, timezone
from supabase import create_client
from postgrest.exceptions import APIError
from supabase_auth.errors import AuthApiError

from .leaderboard import UNNAMED, NameTaken, level
from .session import InvalidSession

PENDING = ('queued', 'grading')


class SupabaseRepository:
    def __init__(self, settings):
        self.client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def resolve_token(self, token):
        try:
            response = self.client.auth.get_user(token)
        except AuthApiError as error:
            # Auth answered and refused this token. Rate limits, 5xx and network
            # failures are outages: they propagate, so nobody is signed out by them.
            if 400 <= error.status < 500 and error.status != 429:
                raise InvalidSession('Invalid session') from error
            raise
        user = response.user if response else None
        if not user or getattr(user, 'is_anonymous', False):
            raise InvalidSession('Invalid session')
        return user.id

    def profile(self, user):
        rows = self.client.table('profiles').select('id,display_name').eq('id', user).execute().data
        if rows:
            return rows[0]
        # Ignore duplicates rather than resetting an existing display name.
        self.client.table('profiles').upsert({'id': user}, on_conflict='id', ignore_duplicates=True).execute()
        return {'id': user, 'display_name': 'player'}

    def prompts(self):
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self.client.table('motions').select('id,text,featured_on').eq('active', True).order('text').execute().data
        return [dict(id=row['id'], text=row['text'], featured=row.get('featured_on') == today) for row in rows]

    def create_motion(self, text, category=None, difficulty=None):
        existing = lambda: self.client.table('motions').select('id').eq('text', text).limit(1).execute().data
        rows = existing()
        if rows:
            return rows[0]['id']
        row = dict(text=text, category=category, difficulty=difficulty)
        try:
            return self.client.table('motions').insert(row).execute().data[0]['id']
        except APIError as error:
            # Two players generated the same question at once: reuse the winner's motion.
            rows = existing() if error.code == '23505' else []
            if rows:
                return rows[0]['id']
            raise

    def set_display_name(self, user, name):
        # Names differing only by case are the same name. '_' is a wildcard in ILIKE.
        pattern = name.replace('_', r'\_')
        if self.client.table('profiles').select('id').ilike('display_name', pattern).neq('id', user).limit(1).execute().data:
            raise NameTaken(name)
        try:
            self.client.table('profiles').upsert({'id': user, 'display_name': name}, on_conflict='id').execute()
        except APIError as error:
            # The pre-check is friendly, but only the unique index resolves races.
            if error.code == '23505':
                raise NameTaken(name) from error
            raise

    def _pages(self, build, page_size=500):
        # PostgREST limits one response to 1000 rows by default. Read every page
        # so unlimited practice does not hide the latest attempt or prior best.
        rows, offset = [], 0
        while True:
            page = build().range(offset, offset + page_size - 1).execute().data
            rows.extend(page)
            if len(page) < page_size:
                return rows
            offset += page_size

    def speeches(self, user):
        return self._pages(lambda: self.client.table('speeches').select('*').eq('player_id', user)
                           .order('created_at').order('id'))

    def board_rows(self):
        """Every graded speech of every named player: scores and letters, never reports or audio."""
        names = {row['id']: row['display_name'] for row in self._pages(
            lambda: self.client.table('profiles').select('id,display_name').neq('display_name', UNNAMED).order('id'))}
        if not names:
            return []
        levels = {row['id']: row['difficulty'] for row in self._pages(
            lambda: self.client.table('motions').select('id,difficulty').order('id'))}
        rows = self._pages(lambda: self.client.table('speeches')
                           .select('id,player_id,motion_id,overall,created_at,letter:report->overall->>letter')
                           .eq('status', 'done').order('created_at').order('id'))
        return [dict(player_id=row['player_id'], display_name=names[row['player_id']], score=float(row['overall']),
                     letter=row.get('letter'), created_at=row['created_at'], difficulty=level(levels.get(row.get('motion_id'))))
                for row in rows if row['player_id'] in names and row.get('overall') is not None]

    def speech(self, id, user):
        rows = self.client.table('speeches').select('*').eq('id', id).eq('player_id', user).limit(1).execute().data
        return rows[0] if rows else None

    def signed_audio(self, path):
        return self.client.storage.from_('audio').create_signed_url(path, 300)['signedURL']

    def upload(self, path, data, content_type):
        self.client.storage.from_('audio').upload(path, data, {'content-type': content_type})

    def remove_audio(self, path):
        self.client.storage.from_('audio').remove([path])

    def download(self, path):
        return self.client.storage.from_('audio').download(path)

    def create_speech(self, row):
        self.client.table('speeches').insert(row).execute()

    def update_speech(self, id, values):
        self.client.table('speeches').update(values).eq('id', id).execute()

    def claim_retry(self, id, user):
        """Queue this owner's failed speech again. None unless it was failed at this moment.

        One conditional UPDATE: of two simultaneous retries only one matches the row.
        """
        rows = (self.client.table('speeches').update({'status': 'queued', 'error': None})
                .eq('id', id).eq('player_id', user).eq('status', 'failed').execute().data)
        return rows[0] if rows else None

    def pending_count(self):
        return self.client.table('speeches').select('id', count='exact').in_('status', PENDING).limit(1).execute().count or 0

    def fail_pending(self, message):
        """Mark every queued or grading speech failed. Only for a stopped worker: see scripts/recover_grading.py."""
        rows = (self.client.table('speeches').update({'status': 'failed', 'error': message})
                .in_('status', PENDING).execute().data)
        return len(rows)
