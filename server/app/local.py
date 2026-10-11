"""Local mode: one player, no accounts. Speeches, prompts and recordings live on disk.

Same interface as SupabaseRepository, so accounts mode is a configuration change:
set the Supabase values instead of LOCAL_MODE=true.
"""
from datetime import datetime, timezone
import json
import threading
from pathlib import Path
from uuid import uuid4

from .leaderboard import board_row
from .session import InvalidSession

LOCAL_TOKEN = 'local-player'
SEED_PROMPTS = ('Should the frontier of AI be paced?',
                'Should homework be banned in elementary school?',
                'Should public transportation be free?')


class LocalRepository:
    def __init__(self, settings):
        self.directory = Path(settings.data_dir) / 'local'
        self.audio_dir = self.directory / 'audio'
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'store.json'
        self.lock = threading.Lock()     # grading runs in a background thread
        if not self.path.exists():
            self._save({'motions': [dict(id=str(uuid4()), text=text, category=None, difficulty=None)
                                    for text in SEED_PROMPTS],
                        'speeches': []})

    def _load(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def _save(self, data):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, indent=1), encoding='utf-8')
        temporary.replace(self.path)

    def resolve_token(self, token):
        if token != LOCAL_TOKEN:
            raise InvalidSession('Invalid local session')
        return LOCAL_TOKEN

    def profile(self, user):
        with self.lock:
            name = self._load().get('display_name', 'Local player')
        return {'id': user, 'display_name': name}

    def set_display_name(self, user, name):
        with self.lock:
            data = self._load()
            data['display_name'] = name
            self._save(data)

    def board_rows(self):
        name = self.profile(LOCAL_TOKEN)['display_name']
        with self.lock:
            levels = {m['id']: m.get('difficulty') for m in self._load()['motions']}
        return [row for row in (board_row(r, name, levels.get(r['motion_id'])) for r in self.speeches(LOCAL_TOKEN)) if row]

    def prompts(self):
        with self.lock:
            motions = self._load()['motions']
        return [dict(id=m['id'], text=m['text'], featured=False) for m in sorted(motions, key=lambda m: m['text'])]

    def create_motion(self, text, category=None, difficulty=None):
        with self.lock:
            data = self._load()
            existing = next((m for m in data['motions'] if m['text'] == text), None)
            if existing:
                return existing['id']
            motion = dict(id=str(uuid4()), text=text, category=category, difficulty=difficulty)
            data['motions'].append(motion)
            self._save(data)
            return motion['id']

    def speeches(self, user):
        with self.lock:
            rows = self._load()['speeches']
        return sorted((r for r in rows if r['player_id'] == user), key=lambda r: (r['created_at'], r['id']))

    def speech(self, id, user):
        return next((r for r in self.speeches(user) if r['id'] == id), None)

    def audio_file(self, path):
        """The recording at a stored path, or None if the path leaves the audio folder."""
        target = (self.audio_dir / path).resolve()
        return target if self.audio_dir.resolve() in target.parents and target.is_file() else None

    def signed_audio(self, path):
        return f'/api/local/audio/{path}'

    def upload(self, path, data, content_type):
        target = self.audio_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def remove_audio(self, path):
        (self.audio_dir / path).unlink(missing_ok=True)

    def download(self, path):
        target = self.audio_file(path)
        if target is None:
            raise FileNotFoundError(path)
        return target.read_bytes()

    def create_speech(self, row):
        with self.lock:
            data = self._load()
            data['speeches'].append(dict(row, created_at=datetime.now(timezone.utc).isoformat(),
                                         report=None, overall=None, error=None))
            self._save(data)

    def update_speech(self, id, values):
        with self.lock:
            data = self._load()
            for row in data['speeches']:
                if row['id'] == id:
                    row.update(values)
            self._save(data)

    def claim_retry(self, id, user):
        """Queue this owner's failed speech again. None unless it was failed at this moment."""
        with self.lock:
            data = self._load()
            row = next((r for r in data['speeches']
                        if r['id'] == id and r['player_id'] == user and r['status'] == 'failed'), None)
            if row is None:
                return None
            row.update(status='queued', error=None)
            self._save(data)
            return dict(row)
