"""Retrying a failed speech from its saved recording, and recovering interrupted gradings."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.settings import Settings
from server.scripts import recover_grading
from test_api import Repository
from test_feedback import SAMPLE

AUDIO = b'RIFFsaved-recording'


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_dir=Path(self.temp.name))
        self.graded = []
        self.fail = True

        def grader(path, prompt, side, cache_dir):
            self.graded.append((Path(path).read_bytes(), Path(path).suffix, prompt, side))
            if self.fail:
                raise RuntimeError('secret provider detail')
            return copy.deepcopy(SAMPLE)

        self.client = TestClient(create_app(self.settings, self.repo, grader))
        self.headers = {'Authorization': 'Bearer good'}
        upload = self.client.post('/api/speeches', headers=self.headers, data={'motion_id': 'm', 'side': 'no'},
                                  files={'audio': ('take.wav', AUDIO, 'audio/wav')})
        self.id = upload.json()['speech_id']
        self.row = self.repo.rows[-1]
        self.assertEqual(self.row['status'], 'failed')

    def tearDown(self):
        self.temp.cleanup()

    def retry(self, id=None, headers=None):
        return self.client.post(f'/api/speeches/{id or self.id}/retry', headers=self.headers if headers is None else headers)

    def test_retry_grades_the_saved_recording_and_keeps_the_speech(self):
        before = dict(id=self.row['id'], created_at=self.row['created_at'], audio_path=self.row['audio_path'])
        self.fail = False
        response = self.retry()
        self.assertEqual((response.status_code, response.json()), (202, {'speech_id': self.id, 'status': 'queued'}))
        self.assertEqual(self.repo.downloads, [before['audio_path']])
        self.assertEqual(self.graded[-1], (AUDIO, '.wav', 'Motion', 'no'))
        self.assertEqual((self.row['status'], self.row['report'], self.row['error']), ('done', SAMPLE, None))
        self.assertEqual({key: self.row[key] for key in before}, before)
        self.assertEqual(len(self.repo.uploads), 1)                      # nothing was uploaded again
        self.assertEqual(len([r for r in self.repo.rows if r['id'] == self.id]), 1)
        card = self.client.get('/api/speeches/' + self.id, headers=self.headers).json()
        self.assertEqual((card['status'], card['can_retry']), ('done', False))

    def test_a_retry_that_fails_again_can_be_retried_again(self):
        self.assertEqual(self.retry().status_code, 202)
        self.assertEqual(self.row['status'], 'failed')
        self.assertNotIn('secret', self.row['error'])
        self.fail = False
        self.assertEqual(self.retry().status_code, 202)
        self.assertEqual(self.row['status'], 'done')

    def test_missing_recording_fails_the_speech_without_grading(self):
        graded = len(self.graded)
        self.repo.uploads.clear()                                        # the stored object is gone
        self.assertEqual(self.retry().status_code, 202)
        self.assertEqual(self.row['status'], 'failed')
        self.assertEqual(len(self.graded), graded)

    def test_only_the_owner_can_retry_and_others_learn_nothing(self):
        self.row['player_id'] = 'other'
        self.assertEqual(self.retry().status_code, 404)
        self.assertEqual(self.retry(headers={}).status_code, 401)
        self.assertEqual(self.retry(headers={'Authorization': 'Bearer wrong'}).status_code, 401)
        self.assertEqual((self.row['status'], self.repo.downloads), ('failed', []))
        self.assertEqual(self.retry(id='no-such-speech').status_code, 404)

    def test_only_failed_speeches_with_a_saved_recording_are_retried(self):
        graded = len(self.graded)
        for status in ('queued', 'grading', 'done', 'ungradable'):
            self.row['status'] = status
            self.assertEqual(self.retry().status_code, 409, status)
        self.assertEqual(self.retry(id='current').status_code, 409)       # graded fixture row
        self.row.update(status='failed', audio_path='owner/take.exe')
        self.assertEqual(self.retry().status_code, 409)
        self.row.update(audio_path=None)
        self.assertEqual(self.retry().status_code, 409)
        self.assertEqual((len(self.graded), self.repo.downloads), (graded, []))

    def test_a_lost_claim_does_not_grade_twice(self):
        graded = len(self.graded)
        self.repo.claim_retry = lambda id, user: None                    # another request won the row
        self.assertEqual(self.retry().status_code, 409)
        self.assertEqual((len(self.graded), self.repo.downloads), (graded, []))

    def test_retired_prompt_and_demo_mode_refuse_retry(self):
        self.repo.prompts = lambda: []
        self.assertEqual(self.retry().status_code, 409)
        self.assertEqual(self.row['status'], 'failed')
        demo = TestClient(create_app(Settings(demo_mode=True, data_dir=Path(self.temp.name) / 'demo')),
                          base_url='http://localhost', client=('127.0.0.1', 1234))
        self.assertEqual(demo.post('/api/speeches/demo-best/retry', headers={'Authorization': 'Bearer local-demo'}).status_code, 503)

    def test_local_mode_retries_from_its_recording_on_disk(self):
        calls = []

        def grader(path, prompt, side, cache_dir):
            calls.append(Path(path).read_bytes())
            if len(calls) == 1:
                raise RuntimeError('provider down')
            return copy.deepcopy(SAMPLE)

        app = create_app(Settings(local_mode=True, data_dir=Path(self.temp.name) / 'local-mode'), grader=grader)
        client = TestClient(app, base_url='http://localhost', client=('127.0.0.1', 1234))
        headers = {'Authorization': 'Bearer local-player'}
        motion = client.get('/api/prompts', headers=headers).json()[0]['id']
        id = client.post('/api/speeches', headers=headers, data={'motion_id': motion, 'side': 'yes'},
                         files={'audio': ('take.wav', AUDIO, 'audio/wav')}).json()['speech_id']
        self.assertTrue(client.get('/api/speeches/' + id, headers=headers).json()['can_retry'])
        self.assertEqual(client.post(f'/api/speeches/{id}/retry', headers=headers).status_code, 202)
        card = client.get('/api/speeches/' + id, headers=headers).json()
        self.assertEqual((card['status'], card['can_retry'], calls), ('done', False, [AUDIO, AUDIO]))
        self.assertEqual(client.post(f'/api/speeches/{id}/retry', headers=headers).status_code, 409)
        self.assertIsNone(app.state.repository.claim_retry(id, 'local-player'))


class Pending:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.messages = []

    def pending_count(self):
        return len([s for s in self.statuses if s in ('queued', 'grading')])

    def fail_pending(self, message):
        self.messages.append(message)
        count = self.pending_count()
        self.statuses = ['failed' if s in ('queued', 'grading') else s for s in self.statuses]
        return count


class RecoveryTests(unittest.TestCase):
    def run_script(self, repository, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = recover_grading.main(list(args), repository)
        return code, out.getvalue()

    def test_dry_run_counts_and_changes_nothing(self):
        repository = Pending(['queued', 'grading', 'done', 'failed', 'ungradable'])
        code, text = self.run_script(repository)
        self.assertEqual(code, 0)
        self.assertIn('2 speech(es)', text)
        self.assertEqual((repository.messages, repository.statuses[:2]), ([], ['queued', 'grading']))

    def test_apply_needs_the_worker_stopped_confirmation(self):
        repository = Pending(['queued'])
        with self.assertRaises(SystemExit):
            self.run_script(repository, '--apply')
        self.assertEqual(repository.statuses, ['queued'])

    def test_apply_fails_only_pending_speeches(self):
        repository = Pending(['queued', 'grading', 'done', 'ungradable'])
        code, text = self.run_script(repository, '--apply', '--worker-stopped')
        self.assertEqual(code, 0)
        self.assertIn('2 interrupted', text)
        self.assertEqual(repository.statuses, ['failed', 'failed', 'done', 'ungradable'])
        self.assertEqual(self.run_script(repository, '--apply', '--worker-stopped')[0], 0)
        self.assertEqual(len(repository.messages), 1)                    # nothing left: no second write


if __name__ == '__main__':
    unittest.main()
