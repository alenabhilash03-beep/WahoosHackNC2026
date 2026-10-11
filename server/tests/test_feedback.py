import copy
import json
from pathlib import Path
import unittest
import sys

from server.app.feedback import losses, top_fixes, previous_best, speech_card, me_card
from server.app.tips import TIPS

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / 'softskills-grader/sample_report.json').read_text())
sys.path.insert(0, str(ROOT / 'softskills-grader'))
from grader.config import load_standards
from grader.scoring import score_all


def speech(id='current', at='2026-10-10T12:00:00Z', report=None, side='yes', motion='m'):
    return dict(id=id, player_id='owner', motion_id=motion, side=side, created_at=at,
                status='graded', report=copy.deepcopy(report or SAMPLE), audio_path='owner/audio.wav')


class FeedbackTests(unittest.TestCase):
    def test_sample_lost_points_match_plan(self):
        self.assertAlmostEqual(sum(item['lost_points'] for item in losses(SAMPLE)), 4.275)
        fixes = top_fixes(SAMPLE)
        self.assertEqual([x['metric'] for x in fixes], ['pitch_variation_pct', 'audience_address_per_100_words', 'uptalk_pct'])
        self.assertEqual(fixes[0]['lost_points'], 2.625)

    def test_every_rubric_tip_exists_and_zeros_are_excluded(self):
        report = copy.deepcopy(SAMPLE)
        for factor in report['factors'].values():
            for metric in factor['metrics'] + factor['deductions']:
                self.assertIn(metric['metric'], TIPS)
            for metric in factor['metrics']:
                metric['score'] = 100
        self.assertEqual(top_fixes(report), [])

    def test_every_critical_rule_has_a_tip(self):
        for factor in SAMPLE['factors'].values():
            for rule in factor['critical']:
                self.assertIn(rule['metric'], TIPS)

    def test_critical_failure_is_the_first_fix_and_losses_still_add_up(self):
        standards, _, _ = load_standards(str(ROOT / 'softskills-grader/config'))
        features = dict(SAMPLE['measurements'], off_topic=1)
        report = dict(copy.deepcopy(SAMPLE), **score_all(features, standards))
        self.assertEqual(report['overall']['score'], 25)
        fixes = top_fixes(report)
        self.assertEqual((fixes[0]['kind'], fixes[0]['metric']), ('critical', 'off_topic'))
        self.assertAlmostEqual(fixes[0]['lost_points'], report['overall']['uncapped'] - 25)
        self.assertAlmostEqual(sum(x['lost_points'] for x in losses(report)), 100 - 25, delta=0.1)

    def test_ungradable_has_no_scores_or_fixes(self):
        report = {'gradable': False, 'overall': {'score': 0, 'letter': 'F'}, 'factors': {}, 'reason': 'Too short'}
        row = speech(report=report)
        card = speech_card(row, [], {'id': 'm', 'text': 'Motion'}, None)
        self.assertEqual(card['fixes'], [])
        self.assertIsNone(card['deltas'])
        me = me_card({'id': 'owner', 'display_name': 'player'}, [row], [])
        self.assertEqual(me['bests'], [])
        self.assertEqual(me['recent_overalls'], [])
        self.assertIsNone(me['speeches'][0]['factor_scores'])
        self.assertIsNone(me['speeches'][0]['overall'])

    def test_previous_best_is_prior_same_prompt_side_tie_earliest(self):
        current = speech()
        early = speech('early', '2026-10-09T11:00:00Z')
        later = speech('later', '2026-10-09T12:00:00Z')
        rows = [speech('future', '2026-10-11T12:00:00Z'), speech('other-side', side='no'),
                speech('other-prompt', motion='other'), later, early]
        self.assertEqual(previous_best(current, rows)['id'], 'early')
        card = speech_card(current, rows, {'id': 'm', 'text': 'Motion'}, 'signed')
        self.assertEqual(card['deltas']['overall'], 0)
        self.assertEqual(card['sections']['delivery']['score'], round((100 + 100 + 83.1)/3, 1))

    def test_clipped_factor_loss_is_attribution_not_recoverable_promise(self):
        report = copy.deepcopy(SAMPLE)
        report['factors']['logic']['score'] = 0
        report['factors']['logic']['base'] = 0
        for metric in report['factors']['logic']['metrics']:
            metric['score'] = 0
        for deduction in report['factors']['logic']['deductions']:
            deduction['points_lost'] = deduction['max_points']
        self.assertGreater(sum(x['lost_points'] for x in losses(report) if x['factor'] == 'logic'), 25)
        self.assertTrue(all('recover' not in x['tip'].lower() for x in top_fixes(report)))

    def test_me_is_recording_order_best_ties_earliest_last_ten(self):
        rows = [speech(str(i), f'2026-10-{i:02d}T12:00:00Z') for i in range(1, 13)]
        me = me_card({'id': 'owner', 'display_name': 'p'}, list(reversed(rows)), [])
        self.assertEqual([s['id'] for s in me['speeches']], [str(i) for i in range(1, 13)])
        self.assertEqual(me['bests'][0]['speech_id'], '1')
        self.assertEqual(len(me['recent_overalls']), 10)

    def test_real_grader_unmeasurable_uptalk_reweights_metric_losses(self):
        standards, _, _ = load_standards(str(ROOT / 'softskills-grader/config'))
        features = {**SAMPLE['measurements'], 'uptalk_pct': None}
        report = {**copy.deepcopy(SAMPLE), **score_all(features, standards)}
        loss_rows = losses(report)
        self.assertFalse(any(item['metric'] == 'uptalk_pct' for item in loss_rows))
        pitch = next(item for item in loss_rows if item['metric'] == 'pitch_variation_pct')
        self.assertAlmostEqual(pitch['lost_points'], 0.2 * 3 / 6 * 35)
        card = speech_card(speech(report=report), [], {'id': 'm', 'text': 'Motion'}, None)
        self.assertIsNone(next(m for m in card['report']['factors']['tone']['metrics'] if m['metric'] == 'uptalk_pct')['score'])

    def test_real_grader_unmeasurable_factor_is_excluded_from_sections_and_me(self):
        standards, _, _ = load_standards(str(ROOT / 'softskills-grader/config'))
        features = {**SAMPLE['measurements'], 'pitch_variation_pct': None, 'uptalk_pct': None, 'hedges_per_100_words': None}
        report = {**copy.deepcopy(SAMPLE), **score_all(features, standards)}
        current = speech(report=report)
        before = speech('before', '2026-10-09T12:00:00Z')
        card = speech_card(current, [before], {'id': 'm', 'text': 'Motion'}, None)
        self.assertEqual(card['sections']['delivery']['score'], 100)
        self.assertIsNone(card['deltas']['factors']['tone'])
        self.assertFalse(any(item['factor'] == 'tone' for item in card['fixes']))
        me = me_card({'id': 'owner', 'display_name': 'p'}, [current], [])
        self.assertNotIn('tone', me['speeches'][0]['factor_scores'])

    def test_mixed_rubrics_missing_factors_have_nullable_deltas(self):
        before = speech('before', '2026-10-09T12:00:00Z')
        del before['report']['factors']['logic']
        current = speech()
        del current['report']['factors']['emotion']
        card = speech_card(current, [before], {'id': 'm', 'text': 'Motion'}, None)
        self.assertIsNone(card['deltas']['logos'])
        self.assertIsNone(card['deltas']['pathos'])
        self.assertIsNone(card['deltas']['factors']['logic'])
        self.assertIsNone(card['deltas']['factors']['emotion'])
        self.assertEqual(card['deltas']['factors']['pace'], 0)


if __name__ == '__main__':
    unittest.main()
