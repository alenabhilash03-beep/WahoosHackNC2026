"""Community rounds: opt-in shared speeches, playback and emoji reactions.

One prompt is open at a time (a round, midnight to midnight UTC). A speaker records it,
then explicitly shares that speech; listeners with an account can play it and leave one
reaction each. Two rankings cover the same shared speeches: highest graded and most reacted.

What the community may see is decided in the database (migration 0006): the round is open,
the entry is shared, review approved it, it has a grade and its recording still exists.
Reports, transcripts, storage paths and who reacted never leave the server.
"""
from datetime import datetime, time, timedelta, timezone
import logging
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .leaderboard import UNNAMED

LOG = logging.getLogger(__name__)
REACTIONS = ('love', 'like', 'fire', 'amazing')


class EntryConflict(Exception):
    """This player already has an entry in the round, or the speech is already entered."""


SORTS = ('grade', 'favorites')
AUDIO_GRACE = timedelta(minutes=5)       # a recording outlives its round by this long, then is deleted
LINK_SECONDS = 300
PAGE_LIMIT = 50
UNAVAILABLE = 'Community rounds are not available on this server.'
CLOSED = 'This community round has closed.'


class EntryRequest(BaseModel):
    speech_id: str


class VisibilityRequest(BaseModel):
    shared: bool


class ReactionRequest(BaseModel):
    reaction: str | None


def utcnow():
    return datetime.now(timezone.utc)


def moment(value):
    """A stored timestamp as an aware datetime."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def is_open(round, now):
    return round is not None and moment(round['starts_at']) <= now < moment(round['ends_at'])


def uuid_or_404(value, what):
    """Ids reach SQL as uuid: anything else is simply not found, never a database error."""
    try:
        return str(UUID(str(value)))
    except ValueError:
        raise HTTPException(404, f'{what} not found.')


def public_round(round):
    if round is None:
        return None
    return dict(id=round['id'], starts_at=round['starts_at'], ends_at=round['ends_at'],
                prompt=dict(id=round['motion_id'], text=round['text']))


def public_entry(entry):
    if entry is None:
        return None
    return dict(id=entry['id'], speech_id=entry['speech_id'], withdrawn_at=entry.get('withdrawn_at'))


def audio_available(row, now):
    """The owner's recording still exists and, for a community speech, has not expired."""
    if not row.get('audio_path') or row.get('audio_deleted_at'):
        return False
    expires = row.get('audio_expires_at')
    return not expires or moment(expires) > now


def card_fields(row):
    """Community state for the owner's card. A private speech has no round and no review."""
    tagged = bool(row.get('community_round_id'))
    return dict(community_round_id=row.get('community_round_id'),
                moderation_status=row.get('moderation_status') if tagged else None,
                audio_expires_at=row.get('audio_expires_at'), audio_deleted_at=row.get('audio_deleted_at'))


def share_problem(row, now):
    """Why this speech cannot be on the community board, or None."""
    if row.get('moderation_status') == 'rejected':
        return 'Review rejected this speech. It cannot appear on the community board.'
    if row.get('status') != 'done' or (row.get('report') or {}).get('gradable') is not True:
        return 'Only a completed, graded speech can be shared.'
    if not audio_available(row, now):
        return 'This recording has expired. Your grade stays saved.'
    return None


def upload_fields(settings, db, user, round_id, motion_id, size, now=None):
    """Extra speech columns for a recording made for a community round, after checking it may be."""
    now = now or utcnow()
    if not available(settings):
        raise HTTPException(503, UNAVAILABLE)
    round = db.community_round(uuid_or_404(round_id, 'Community round'))
    if round is None:
        raise HTTPException(404, 'Community round not found.')
    if not is_open(round, now):
        raise HTTPException(409, CLOSED + ' Start again with the next prompt.')
    if round['motion_id'] != motion_id:
        raise HTTPException(422, 'This recording is not for the current community prompt.')
    if size > settings.community_max_audio_bytes:
        raise HTTPException(413, f'A community recording can be at most {settings.community_max_audio_bytes // (1024 * 1024)} MB.')
    if db.community_attempts(round['id'], user) >= settings.community_attempts_per_round:
        raise HTTPException(429, f'You have used all {settings.community_attempts_per_round} recordings for this round. '
                                 'Share one of them, or wait for the next prompt.')
    return dict(community_round_id=round['id'],
                audio_expires_at=(moment(round['ends_at']) + AUDIO_GRACE).isoformat())


def available(settings):
    return settings.community_enabled and not (settings.demo_mode or settings.local_mode)


def day_window(day):
    start = datetime.combine(day, time(0), tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def ensure_rounds(db, days=2, motions=None, now=None, apply=True):
    """Make sure each of the next `days` UTC days has a round. Safe to run again and again.

    A day that already has a round is left alone. Otherwise its prompt is the next id in
    `motions` if given, else that day's Daily Prompt, else the next curated prompt in rotation.
    Today's round also becomes today's Daily Prompt. Returns the plan: [{day, motion_id, created}].
    """
    now = now or utcnow()
    today = now.date()
    existing = db.rounds_from(day_window(today)[0])
    pool = [motion['id'] for motion in db.curated_motions()]
    chosen, plan = list(motions or []), []
    for offset in range(days):
        day = today + timedelta(days=offset)
        start, end = day_window(day)
        covering = next((r for r in existing if moment(r['starts_at']) < end and moment(r['ends_at']) > start), None)
        if covering:
            plan.append(dict(day=day.isoformat(), motion_id=covering['motion_id'], created=False))
            continue
        motion = chosen.pop(0) if chosen else db.motion_featured_on(day) or (pool[day.toordinal() % len(pool)] if pool else None)
        if motion is None:
            LOG.error('No prompt available for the community round on %s', day)
            continue
        created = bool(db.create_round(motion, start, end)) if apply else True
        plan.append(dict(day=day.isoformat(), motion_id=motion, created=created))
    if apply:
        current = db.current_round(now)
        if current and db.motion_featured_on(today) != current['motion_id']:
            db.feature_motion(current['motion_id'], today)
    return plan


def cleanup_audio(db, now=None, limit=50, dry_run=False):
    """Delete recordings of closed rounds. Returns {expired, removed, failed}.

    Only speeches tagged for a community round whose expiry has passed are touched; private
    practice recordings never are. The file goes first. If that fails the path is kept, so
    the next run retries; a file that is already gone counts as removed.
    """
    now = now or utcnow()
    rows = db.expired_community_audio(now, limit)
    result = dict(expired=len(rows), removed=0, failed=0)
    if dry_run:
        return result
    for row in rows:
        try:
            db.remove_audio(row['audio_path'])
            db.finish_audio_cleanup(row['id'], row['audio_path'], now)
            result['removed'] += 1
        except Exception as exc:
            result['failed'] += 1
            LOG.error('Community audio cleanup failed for %s (%s)', row['id'], type(exc).__name__)
    return result


def maintain(db, days=2):
    """One pass of the background upkeep: rounds for today and tomorrow, then expired audio."""
    plan = ensure_rounds(db, days=days)
    cleaned = cleanup_audio(db)
    created = len([item for item in plan if item['created']])
    if created or cleaned['expired']:
        LOG.info('Community upkeep: %s round(s) created; audio expired=%s removed=%s failed=%s',
                 created, cleaned['expired'], cleaned['removed'], cleaned['failed'])
    return plan, cleaned


def register(app, settings, repo, user):
    """Add the community routes. Every one resolves the caller from the bearer token."""

    def enabled():
        if not available(settings):
            raise HTTPException(503, UNAVAILABLE)

    def display_name(db, uid):
        name = db.profile(uid)['display_name']
        return None if name == UNNAMED else name

    @app.get('/api/community/round', dependencies=[Depends(enabled)])
    def current(uid=Depends(user), db=Depends(repo)):
        round = db.current_round()
        return dict(round=public_round(round),
                    entry=public_entry(db.entry_for(round['id'], uid)) if round else None,
                    display_name=display_name(db, uid))

    @app.post('/api/community/entries', dependencies=[Depends(enabled)])
    def share(body: EntryRequest, uid=Depends(user), db=Depends(repo)):
        """Share the caller's own graded speech from the open round. Repeating the request changes nothing."""
        speech_id, now = uuid_or_404(body.speech_id, 'Speech'), utcnow()
        row = db.speech(speech_id, uid)
        if row is None:
            raise HTTPException(404, 'Speech not found.')
        if not row.get('community_round_id'):
            raise HTTPException(409, 'Private practice cannot be shared. Record the current community prompt.')
        round = db.current_round(now)
        if round is None or round['id'] != row['community_round_id']:
            raise HTTPException(409, CLOSED + ' Your grade stays saved.')
        entry = db.entry_for(round['id'], uid)
        if entry and entry['speech_id'] != speech_id:
            raise HTTPException(409, 'You already shared another speech in this round. You can only re-share that speech.')
        problem = share_problem(row, now)
        if problem:
            raise HTTPException(409, problem)
        if display_name(db, uid) is None:
            raise HTTPException(409, 'Choose a display name before sharing.')
        status = 200
        if entry is None:
            try:
                entry, status = db.create_entry(round['id'], speech_id, uid), 201
            except EntryConflict:
                # A second tab got there first. The same speech is fine; a different one is not.
                entry = db.entry_for(round['id'], uid)
                if entry is None or entry['speech_id'] != speech_id:
                    raise HTTPException(409, 'You already shared another speech in this round. You can only re-share that speech.')
        if entry.get('withdrawn_at'):
            entry = db.set_entry_visibility(entry['id'], uid, True)
        if settings.community_auto_approve and row.get('moderation_status') == 'pending':
            db.review(speech_id, 'approved')
        db.set_published(speech_id, uid, True)
        return JSONResponse({'entry': public_entry(entry)}, status_code=status)

    @app.post('/api/community/entries/{entry_id}/visibility', dependencies=[Depends(enabled)])
    def visibility(entry_id: str, body: VisibilityRequest, uid=Depends(user), db=Depends(repo)):
        """Withdraw the caller's entry, or share that same speech again while its round is open."""
        entry = db.entry(uuid_or_404(entry_id, 'Entry'), uid)
        if entry is None:
            raise HTTPException(404, 'Entry not found.')
        if body.shared:
            now = utcnow()
            round = db.current_round(now)
            if round is None or round['id'] != entry['round_id']:
                raise HTTPException(409, CLOSED + ' Your grade stays saved.')
            row = db.speech(entry['speech_id'], uid)
            problem = share_problem(row, now) if row else 'Speech not found.'
            if problem:
                raise HTTPException(409, problem)
        entry = db.set_entry_visibility(entry['id'], uid, body.shared)
        if entry is None:
            raise HTTPException(404, 'Entry not found.')
        db.set_published(entry['speech_id'], uid, body.shared)
        return {'entry': public_entry(entry)}

    @app.get('/api/community/leaderboard', dependencies=[Depends(enabled)])
    def board(sort: str = 'grade', limit: int = Query(default=20, ge=1, le=PAGE_LIMIT),
              offset: int = Query(default=0, ge=0, le=100000), round_id: str | None = None,
              uid=Depends(user), db=Depends(repo)):
        if sort not in SORTS:
            raise HTTPException(422, 'Sort must be grade or favorites.')
        if round_id is None:
            round = db.current_round()
        else:
            round = db.community_round(uuid_or_404(round_id, 'Community round'))
            if round is None:
                raise HTTPException(404, 'Community round not found.')
        if not is_open(round, utcnow()):
            return dict(round=public_round(round), entries=[], has_more=False)
        # One row beyond the page says whether another page exists.
        rows = db.community_board(round['id'], uid, sort, limit + 1, offset)
        return dict(round=public_round(round), entries=[board_entry(row) for row in rows[:limit]], has_more=len(rows) > limit)

    @app.get('/api/community/speeches/{speech_id}/audio', dependencies=[Depends(enabled)])
    def audio(speech_id: str, uid=Depends(user), db=Depends(repo)):
        """A short-lived link to a shared recording. It never outlives the round."""
        found = db.community_audio(uuid_or_404(speech_id, 'Recording'))
        now = utcnow()
        seconds = min(LINK_SECONDS, int((moment(found['ends_at']) - now).total_seconds())) if found else 0
        if seconds <= 0:
            raise HTTPException(404, 'Recording not found.')
        return dict(audio_url=db.signed_audio(found['audio_path'], seconds),
                    expires_at=(now + timedelta(seconds=seconds)).isoformat())

    @app.post('/api/community/speeches/{speech_id}/reaction', dependencies=[Depends(enabled)])
    def reaction(speech_id: str, body: ReactionRequest, uid=Depends(user), db=Depends(repo)):
        """Set, change or (with null) remove the caller's one reaction. Returns the counted totals."""
        speech_id = uuid_or_404(speech_id, 'Speech')
        if body.reaction is not None and body.reaction not in REACTIONS:
            raise HTTPException(422, 'Reaction must be love, like, fire or amazing.')
        db.profile(uid)                      # a listener who has only ever opened the board has no profile row yet
        result = db.react(speech_id, uid, body.reaction, settings.community_reactions_per_minute) or {}
        status = result.get('status')
        if status == 'ok':
            return reaction_result(result)
        if status == 'own':
            raise HTTPException(403, 'You cannot react to your own speech.')
        if status == 'closed':
            raise HTTPException(409, CLOSED)
        if status == 'invalid':
            raise HTTPException(422, 'Reaction must be love, like, fire or amazing.')
        if status == 'limited':
            wait = int(result.get('retry_after') or 60)
            raise HTTPException(429, f'Too many reactions. Try again in {wait} seconds.', headers={'Retry-After': str(wait)})
        raise HTTPException(404, 'Speech not found.')


def reaction_result(row):
    counts = row.get('counts') or {}
    return dict(counts={name: int(counts.get(name) or 0) for name in REACTIONS},
                total_reactions=int(row.get('total_reactions') or 0), my_reaction=row.get('my_reaction'))


def board_entry(row):
    """One board row: ids, name, side, grade and counts. Nothing private."""
    return dict(id=row['id'], speech_id=row['speech_id'], rank=int(row['rank']), display_name=row['display_name'],
                side=row['side'], overall=float(row['overall']), letter=row.get('letter'),
                is_owner=bool(row.get('is_owner')), **reaction_result(row))
