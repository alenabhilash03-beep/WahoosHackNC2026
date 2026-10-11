"""Service-role operations. Every speech query explicitly scopes its owner."""
from datetime import datetime, timezone
from supabase import create_client
from postgrest.exceptions import APIError
from supabase_auth.errors import AuthApiError

from .community import EntryConflict
from .leaderboard import UNNAMED, NameTaken, level
from .session import InvalidSession

PENDING = ('queued', 'grading')
ROUND = 'id,motion_id,starts_at,ends_at,motion:motions(text)'
ENTRY = 'id,round_id,speech_id,player_id,shared_at,withdrawn_at'


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

    def signed_audio(self, path, seconds=300):
        return self.client.storage.from_('audio').create_signed_url(path, seconds)['signedURL']

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

    # ---- community rounds, shared entries and reactions (migration 0006) ----
    # Ranking, playback and reactions are decided by SQL functions, so every caller
    # applies the same eligibility rule and counts cannot be raced from here.

    @staticmethod
    def _round(row):
        return dict(id=row['id'], motion_id=row['motion_id'], starts_at=row['starts_at'], ends_at=row['ends_at'],
                    text=(row.get('motion') or {}).get('text'))

    def current_round(self, now=None):
        moment = (now or datetime.now(timezone.utc)).isoformat()
        rows = (self.client.table('community_rounds').select(ROUND)
                .lte('starts_at', moment).gt('ends_at', moment).limit(1).execute().data)
        return self._round(rows[0]) if rows else None

    def community_round(self, id):
        rows = self.client.table('community_rounds').select(ROUND).eq('id', id).limit(1).execute().data
        return self._round(rows[0]) if rows else None

    def rounds_from(self, start):
        """Rounds that end after this moment, earliest first."""
        rows = (self.client.table('community_rounds').select(ROUND).gt('ends_at', start.isoformat())
                .order('starts_at').execute().data)
        return [self._round(row) for row in rows]

    def create_round(self, motion_id, starts_at, ends_at):
        """A new round's id, or None when another round already covers part of that window."""
        row = dict(motion_id=motion_id, starts_at=starts_at.isoformat(), ends_at=ends_at.isoformat())
        try:
            return self.client.table('community_rounds').insert(row).execute().data[0]['id']
        except APIError as error:
            if error.code == '23P01':        # the no-overlap exclusion constraint
                return None
            raise

    def curated_motions(self):
        """Hand-written prompts (no category): the pool community rounds rotate through."""
        return (self.client.table('motions').select('id,text').eq('active', True)
                .is_('category', 'null').order('text').execute().data)

    def motion_featured_on(self, day):
        rows = (self.client.table('motions').select('id').eq('featured_on', day.isoformat())
                .eq('active', True).limit(1).execute().data)
        return rows[0]['id'] if rows else None

    def feature_motion(self, motion_id, day):
        """Make this the Daily Prompt for a day. featured_on is unique, so the previous holder is cleared first."""
        (self.client.table('motions').update({'featured_on': None})
         .eq('featured_on', day.isoformat()).neq('id', motion_id).execute())
        self.client.table('motions').update({'featured_on': day.isoformat()}).eq('id', motion_id).execute()

    def community_attempts(self, round_id, user):
        return (self.client.table('speeches').select('id', count='exact').eq('community_round_id', round_id)
                .eq('player_id', user).limit(1).execute().count or 0)

    def entry_for(self, round_id, user):
        rows = (self.client.table('community_entries').select(ENTRY).eq('round_id', round_id)
                .eq('player_id', user).limit(1).execute().data)
        return rows[0] if rows else None

    def entry(self, id, user):
        rows = (self.client.table('community_entries').select(ENTRY).eq('id', id)
                .eq('player_id', user).limit(1).execute().data)
        return rows[0] if rows else None

    def create_entry(self, round_id, speech_id, user):
        row = dict(round_id=round_id, speech_id=speech_id, player_id=user)
        try:
            return self.client.table('community_entries').insert(row).execute().data[0]
        except APIError as error:
            # One entry per player per round and per speech: the database settles a double submit.
            if error.code == '23505':
                raise EntryConflict() from error
            raise

    def set_entry_visibility(self, id, user, shared):
        moment = None if shared else datetime.now(timezone.utc).isoformat()
        rows = (self.client.table('community_entries').update({'withdrawn_at': moment})
                .eq('id', id).eq('player_id', user).execute().data)
        return rows[0] if rows else None

    def set_published(self, speech_id, user, published):
        (self.client.table('speeches').update({'published': published})
         .eq('id', speech_id).eq('player_id', user).execute())

    def review(self, speech_id, status):
        """Record a moderation decision. True if the speech exists."""
        values = {'moderation_status': status}
        if status == 'rejected':
            values['published'] = False
        return bool(self.client.table('speeches').update(values).eq('id', speech_id).execute().data)

    def pending_reviews(self):
        """Shared entries waiting for review, oldest first, with what a reviewer needs to listen."""
        entries = (self.client.table('community_entries').select(ENTRY).is_('withdrawn_at', 'null')
                   .order('shared_at').execute().data)
        if not entries:
            return []
        speeches = {row['id']: row for row in self.client.table('speeches')
                    .select('id,audio_path,overall,side,status')
                    .in_('id', [entry['speech_id'] for entry in entries])
                    .eq('moderation_status', 'pending').execute().data}
        names = {row['id']: row['display_name'] for row in self.client.table('profiles').select('id,display_name')
                 .in_('id', list({entry['player_id'] for entry in entries})).execute().data}
        return [dict(entry, display_name=names.get(entry['player_id']),
                     **{key: speeches[entry['speech_id']][key] for key in ('audio_path', 'overall', 'side', 'status')})
                for entry in entries if entry['speech_id'] in speeches]

    def community_board(self, round_id, user, sort, limit, offset):
        return self.client.rpc('community_board', dict(p_round=round_id, p_user=user, p_sort=sort,
                                                       p_limit=limit, p_offset=offset)).execute().data or []

    def community_audio(self, speech_id):
        """{audio_path, ends_at} for a recording the community may hear right now, else None."""
        return self.client.rpc('community_audio', {'p_speech': speech_id}).execute().data or None

    def react(self, speech_id, user, reaction, per_minute):
        return self.client.rpc('community_react', dict(p_speech=speech_id, p_user=user, p_reaction=reaction,
                                                       p_per_minute=per_minute)).execute().data

    def expired_community_audio(self, now, limit):
        """Community recordings past their expiry whose file has not been removed yet."""
        return (self.client.table('speeches').select('id,audio_path').not_.is_('community_round_id', 'null')
                .not_.is_('audio_path', 'null').lt('audio_expires_at', now.isoformat())
                .order('audio_expires_at').limit(limit).execute().data)

    def finish_audio_cleanup(self, speech_id, path, now):
        """After the file is gone: drop the entry with its reactions, then forget the path and unpublish.

        The path is cleared last. If any step fails, the speech is still listed as expired
        and the next run repeats all of them; each one is harmless to repeat.
        """
        self.client.table('community_entries').delete().eq('speech_id', speech_id).execute()
        (self.client.table('speeches')
         .update({'audio_path': None, 'audio_deleted_at': now.isoformat(), 'published': False})
         .eq('id', speech_id).eq('audio_path', path).execute())
