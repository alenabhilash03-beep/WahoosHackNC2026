"""Production route-to-grader proof using synthetic audio and canned providers.

Audio decoding, acoustics, feature extraction, scoring, background persistence,
cards and history all run unchanged. This fixture is not a real spoken speech.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.settings import Settings
from test_api import Repository

ROOT = Path(__file__).resolve().parents[2]


class RecordingRepository(Repository):
    def __init__(self):
        super().__init__()
        self.rows = []

    def create_speech(self, row):
        super().create_speech(row)
        self.rows[-1]['created_at'] = f'2026-10-10T12:{len(self.rows):02d}:00Z'


class PipelineIntegrationTests(unittest.TestCase):
    def test_twice_uploaded_synthetic_audio_flows_through_real_grader_card_and_me(self):
        spec = importlib.util.spec_from_file_location('arena_pipeline_fixture', ROOT / 'softskills-grader/tests/fixtures.py')
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        with tempfile.TemporaryDirectory(prefix='solo-pipeline-') as directory:
            fixture = fixtures.build(directory)
            audio = Path(fixture['audio']).read_bytes()
            repository = RecordingRepository()
            # Match the canned provider's prompt; the side is resolved from the
            # actual form and the owner from the actual bearer dependency.
            repository.prompts = lambda: [{'id': 'm', 'text': fixture['topic'], 'featured': False}]
            client = TestClient(create_app(Settings(data_dir=Path(directory) / 'data'), repository))
            headers = {'Authorization': 'Bearer good'}
            from grader import pipeline
            with patch.object(pipeline, 'transcribe', return_value=fixture['scribe']) as transcribe, \
                    patch.object(pipeline, 'extract', return_value=fixture['annotations']) as extract:
                ids = []
                for _ in range(2):
                    response = client.post('/api/speeches', headers=headers,
                                           data={'motion_id': 'm', 'side': 'yes'},
                                           files={'audio': ('synthetic.wav', audio, 'audio/wav')})
                    self.assertEqual(response.status_code, 202)
                    ids.append(response.json()['speech_id'])
                self.assertEqual(transcribe.call_count, 2)
                self.assertEqual(extract.call_count, 2)

            self.assertEqual([row['status'] for row in repository.rows], ['done', 'done'])
            reports = [json.dumps(row['report'], sort_keys=True, separators=(',', ':'), allow_nan=False)
                       for row in repository.rows]
            self.assertEqual(reports[0], reports[1])
            self.assertEqual(repository.uploads[0][1], audio)
            self.assertEqual(repository.uploads[1][1], audio)

            response = client.get(f'/api/speeches/{ids[1]}', headers=headers)
            self.assertEqual(response.status_code, 200)
            card = response.json()
            self.assertTrue(card['report']['gradable'])
            self.assertEqual(len(card['report']['words']), 155)
            self.assertTrue(all(word['end'] >= word['start'] >= 0 for word in card['report']['words']))
            rubric_rows = sum(len(factor['metrics']) + len(factor['deductions'])
                              for factor in card['report']['factors'].values())
            self.assertEqual(rubric_rows, 25)
            self.assertEqual(len(card['fixes']), 3)
            self.assertEqual(card['previous_best']['id'], ids[0])
            self.assertEqual(card['deltas']['overall'], 0)
            self.assertEqual(card['deltas']['factors'], {key: 0 for key in card['report']['factors']})
            self.assertEqual(card['report'], repository.rows[1]['report'])
            self.assertEqual(set(card['sections']), {'logos', 'pathos', 'delivery'})

            me_response = client.get('/api/me', headers=headers)
            self.assertEqual(me_response.status_code, 200)
            me = me_response.json()
            self.assertEqual([speech['id'] for speech in me['speeches']], ids)
            self.assertTrue(all(len(speech['factor_scores']) == 5 for speech in me['speeches']))
            self.assertEqual(me['bests'][0]['speech_id'], ids[0])
            self.assertEqual([row['speech_id'] for row in me['recent_overalls']], ids)
            self.assertFalse(card.get('demo', False))


if __name__ == '__main__':
    unittest.main()
