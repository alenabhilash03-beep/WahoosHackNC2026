"""Explicit local fixtures: sample report and synthetic tone, never real grading."""
import copy
import importlib.util
import json
import math
import shutil
from .leaderboard import board_row
from .session import InvalidSession
from .settings import ROOT


class DemoRepository:
    def __init__(self, settings):
        self.directory = settings.data_dir
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'demo.json'
        fixture_path = self.directory / 'fixture'
        # Reuse the original deterministic waveform and canned Scribe timeline
        # that produced sample_report.json. These are explicitly synthetic.
        spec = importlib.util.spec_from_file_location('arena_demo_fixture', ROOT / 'softskills-grader/tests/fixtures.py')
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        if not (fixture_path / 'speech.wav').exists() or not (fixture_path / 'scribe.json').exists():
            fixtures.build(str(fixture_path))
        scribe = json.loads((fixture_path / 'scribe.json').read_text(encoding='utf-8'))
        words = [dict(text=word['text'], start=word['start'], end=word['end'], confidence=math.exp(word['logprob']))
                 for word in scribe['words'] if word.get('type') == 'word']
        if not self.path.exists():
            sample = json.loads((ROOT / 'softskills-grader/sample_report.json').read_text(encoding='utf-8'))
            first = copy.deepcopy(sample)
            # A lower-scoring display fixture, explicitly marked synthetic.
            for factor in first['factors'].values():
                factor['score'] = round(max(0, factor['score'] - 15), 1)
                factor['base'] = round(max(0, factor['base'] - 15), 1)
                for metric in factor['metrics']:
                    metric['score'] = max(0, metric['score'] - 15)
            first['overall'] = {'score': 80.7, 'letter': 'B-'}
            short = {'gradable': False, 'reason': 'Sample ungradable attempt: fewer than 40 words.', 'overall': {'score': 0, 'letter': 'F'}, 'factors': {}, 'transcript': 'A short sample.', 'evidence': {}}
            rows = []
            for id, time, report in [('demo-first', '2026-10-09T10:00:00Z', first), ('demo-best', '2026-10-10T10:00:00Z', sample), ('demo-ungradable', '2026-10-10T11:00:00Z', short)]:
                rows.append(dict(id=id, player_id='demo-player', motion_id='demo-ai-pacing', side='yes', created_at=time,
                                 status='done' if report['gradable'] else 'ungradable', report=report, audio_path='synthetic.wav'))
            self.path.write_text(json.dumps(rows, indent=2), encoding='utf-8')
        self.rows = json.loads(self.path.read_text(encoding='utf-8'))
        for row in self.rows:
            report = row['report']
            report['demo'] = {'synthetic': True,
                              'audio': 'Synthetic buzz follows a canned timeline; this is not real speech.',
                              'timing': 'Synthetic fixture timings, not measured real speech.'}
            if report.get('gradable'):
                report['words'] = copy.deepcopy(words)
        self.path.write_text(json.dumps(self.rows, indent=2), encoding='utf-8')
        self.audio = self.directory / 'synthetic.wav'
        shutil.copyfile(fixture_path / 'speech.wav', self.audio)

    def resolve_token(self, token):
        if token != 'local-demo':
            raise InvalidSession('Invalid demo session')
        return 'demo-player'

    def profile(self, user):
        return {'id': user, 'display_name': 'Local demo'}

    def prompts(self):
        return [{'id': 'demo-ai-pacing', 'text': 'Should the frontier of AI be paced?', 'featured': True}]

    def speeches(self, user):
        return [copy.deepcopy(row) for row in self.rows if row['player_id'] == user]

    def board_rows(self):
        return [row for row in (board_row(r, 'Local demo') for r in self.rows) if row]

    def speech(self, id, user):
        return next((copy.deepcopy(row) for row in self.rows if row['id'] == id and row['player_id'] == user), None)

    def signed_audio(self, path):
        return '/api/demo/audio'
