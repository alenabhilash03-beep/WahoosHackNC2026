"""Service-role operations. Every speech query explicitly scopes its owner."""
from datetime import datetime, timezone
from supabase import create_client

from .leaderboard import UNNAMED, NameTaken


class SupabaseRepository:
    def __init__(self, settings):
        self.client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def resolve_token(self, token):
        user = self.client.auth.get_user(token).user
        if not user:
            raise ValueError('Invalid session')
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
        rows = self.client.table('motions').select('id').eq('text', text).limit(1).execute().data
        if rows:
            return rows[0]['id']
        row = dict(text=text, category=category, difficulty=difficulty)
        return self.client.table('motions').insert(row).execute().data[0]['id']

    def set_display_name(self, user, name):
        # Names differing only by case are the same name. '_' is a wildcard in ILIKE.
        pattern = name.replace('_', r'\_')
        if self.client.table('profiles').select('id').ilike('display_name', pattern).neq('id', user).limit(1).execute().data:
            raise NameTaken(name)
        self.client.table('profiles').upsert({'id': user, 'display_name': name}, on_conflict='id').execute()

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
        rows = self._pages(lambda: self.client.table('speeches')
                           .select('id,player_id,overall,created_at,letter:report->overall->>letter')
                           .eq('status', 'done').order('created_at').order('id'))
        return [dict(player_id=row['player_id'], display_name=names[row['player_id']], score=float(row['overall']),
                     letter=row.get('letter'), created_at=row['created_at'])
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

    def create_speech(self, row):
        self.client.table('speeches').insert(row).execute()

    def update_speech(self, id, values):
        self.client.table('speeches').update(values).eq('id', id).execute()
