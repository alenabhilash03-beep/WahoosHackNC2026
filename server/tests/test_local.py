from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.questions import UnsuitableCategory, clean_category, fallback, valid_question
from server.app.settings import Settings
from test_feedback import SAMPLE

HEADERS = {'Authorization': 'Bearer local-player'}


class LocalModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.asked = []

        def maker(category, difficulty):
            self.asked.append((category, difficulty))
            if category == 'Nope':
                raise UnsuitableCategory('That category does not work for a debate question. Try another one.')
            return f'Should {category.lower()} be on every menu?'

        def grader(path, prompt, side, cache_dir):
            return dict(SAMPLE, inputs=dict(SAMPLE['inputs'], topic=prompt, side=side))

        self.app = create_app(Settings(local_mode=True, data_dir=Path(self.temp.name)), grader=grader, question_maker=maker)
        self.client = TestClient(self.app, base_url='http://localhost', client=('127.0.0.1', 1234))

    def tearDown(self):
        self.temp.cleanup()

    def test_config_announces_local_mode_without_supabase_values(self):
        body = self.client.get('/config.js').text
        self.assertIn('"LOCAL_MODE": true', body)
        self.assertIn('"SUPABASE_URL": ""', body)

    def test_only_this_computer_and_the_local_token_are_accepted(self):
        remote = TestClient(self.app, base_url='http://localhost', client=('192.0.2.1', 1234))
        self.assertEqual(remote.get('/api/prompts', headers=HEADERS).status_code, 403)
        self.assertEqual(self.client.get('/api/prompts', headers={'Authorization': 'Bearer other'}).status_code, 401)
        self.assertEqual(len(self.client.get('/api/prompts', headers=HEADERS).json()), 3)  # seeded

    def test_question_becomes_a_prompt_once(self):
        first = self.client.post('/api/questions', headers=HEADERS, json={'category': '  Pizza <b>!', 'difficulty': 'beginner'})
        self.assertEqual(first.status_code, 200)
        motion = first.json()
        self.assertEqual((motion['text'], motion['category'], motion['difficulty']), ('Should pizza b be on every menu?', 'Pizza b', 'beginner'))
        again = self.client.post('/api/questions', headers=HEADERS, json={'category': 'Pizza b', 'difficulty': 'beginner'}).json()
        self.assertEqual(again['id'], motion['id'])
        prompts = self.client.get('/api/prompts', headers=HEADERS).json()
        self.assertIn(motion['id'], [p['id'] for p in prompts])
        self.assertEqual(len(prompts), 4)

    def test_bad_question_requests_are_refused_with_a_reason(self):
        self.assertEqual(self.client.post('/api/questions', headers=HEADERS, json={'category': 'Pizza', 'difficulty': 'extreme'}).status_code, 422)
        self.assertEqual(self.client.post('/api/questions', headers=HEADERS, json={'category': '!', 'difficulty': 'beginner'}).status_code, 422)
        refused = self.client.post('/api/questions', headers=HEADERS, json={'category': 'Nope', 'difficulty': 'expert'})
        self.assertEqual(refused.status_code, 422)
        self.assertIn('Try another', refused.json()['detail'])

    def test_recording_is_stored_graded_and_played_back(self):
        motion = self.client.post('/api/questions', headers=HEADERS, json={'category': 'Pizza', 'difficulty': 'intermediate'}).json()
        upload = self.client.post('/api/speeches', headers=HEADERS, data={'motion_id': motion['id'], 'side': 'no'},
                                  files={'audio': ('a.webm', b'webm-bytes', 'audio/webm')})
        self.assertEqual(upload.status_code, 202)
        speech_id = upload.json()['speech_id']
        for _ in range(50):
            card = self.client.get(f'/api/speeches/{speech_id}', headers=HEADERS).json()
            if card['status'] == 'done':
                break
            time.sleep(0.05)
        self.assertEqual(card['status'], 'done')
        self.assertEqual(card['prompt']['text'], motion['text'])
        self.assertEqual(card['report']['inputs']['side'], 'no')
        self.assertEqual(self.client.get(card['audio_url']).content, b'webm-bytes')
        me = self.client.get('/api/me', headers=HEADERS).json()
        self.assertEqual(len(me['recent_overalls']), 1)

    def test_audio_route_stays_inside_the_audio_folder(self):
        self.client.get('/api/prompts', headers=HEADERS)              # creates the store
        self.assertEqual(self.client.get('/api/local/audio/../store.json').status_code, 404)
        self.assertEqual(self.client.get('/api/local/audio/%2e%2e/store.json').status_code, 404)


class QuestionRuleTests(unittest.TestCase):
    def test_categories_are_cleaned_and_bounded(self):
        self.assertEqual(clean_category('  Video\nGames!!  '), 'Video Games')
        self.assertEqual(len(clean_category('x' * 100)), 40)
        with self.assertRaises(ValueError):
            clean_category(' ? ')

    def test_only_short_yes_no_questions_pass(self):
        self.assertTrue(valid_question('Should schools ban phones?'))
        self.assertFalse(valid_question('Why do schools ban phones?'))
        self.assertFalse(valid_question('Should schools ban phones'))
        self.assertFalse(valid_question('Should ' + 'very ' * 25 + 'long questions pass?'))
        for level in ('beginner', 'intermediate', 'expert'):
            self.assertTrue(valid_question(fallback('Board Games', level)))


if __name__ == '__main__':
    unittest.main()
