"""Real transcript timings and acoustic evidence; no external API calls."""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures
from grader import grade
from grader.audio import VoiceTrack
from grader.config import load_standards
from grader.features import tone_metrics, words_from_scribe


class TimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="grader-timing-")
        cls.fx = fixtures.build(cls.folder.name)
        cls.standards, cls.lexicons, _ = load_standards(str(ROOT / "config"))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def report(self, scribe=None):
        return grade(self.fx["audio"], self.fx["topic"], "yes",
                     transcript=scribe or self.fx["scribe"], extraction=self.fx["annotations"],
                     cache_dir=self.folder.name)

    def test_gradable_report_keeps_true_word_timings_and_full_transcript(self):
        report = self.report()
        source = [w for w in self.fx["scribe"]["words"] if w["type"] == "word"]
        expected = [{"text": w["text"], "start": w["start"], "end": w["end"],
                     "confidence": math.exp(w["logprob"])} for w in source]
        self.assertEqual(report["words"], expected)
        self.assertEqual(report["transcript"], self.fx["scribe"]["text"])
        self.assertEqual(self.report(), report)
        sample = json.loads((ROOT / "sample_report.json").read_text())
        self.assertEqual(report["measurements"], sample["measurements"])
        self.assertEqual(report["factors"], sample["factors"])

    def test_short_report_keeps_words_and_unknown_confidence(self):
        scribe = {"text": "Um, I agree.", "words": [
            {"type": "word", "text": "Um,", "start": 0.3, "end": 0.6, "logprob": -0.3},
            {"type": "spacing", "text": " ", "start": 0.6, "end": 0.8},
            {"type": "word", "text": "I", "start": 0.8, "end": 0.9},
            {"type": "word", "text": "agree.", "start": 1, "end": 1.4, "logprob": float("nan")},
        ]}
        report = self.report(scribe)
        self.assertFalse(report["gradable"])
        self.assertEqual(report["transcript"], scribe["text"])
        self.assertEqual(len(report["words"]), 3)
        self.assertIsNone(report["words"][1]["confidence"])
        self.assertIsNone(report["words"][2]["confidence"])
        json.dumps(report, allow_nan=False)

    def test_invalid_word_timings_are_skipped_without_losing_transcript(self):
        valid = {"type": "word", "text": "kept!", "start": 0.4, "end": 0.8, "logprob": -0.2}
        invalid = [None, {}, {**valid, "start": None}, {**valid, "start": "bad"},
                   {**valid, "start": float("nan")}, {**valid, "end": float("inf")},
                   {**valid, "start": -1}, {**valid, "end": 0.2}, {**valid, "start": True},
                   {**valid, "text": None}, {**valid, "text": "..."}]
        scribe = {"text": "Every original word stays in this transcript.", "words": [valid, *invalid]}
        words = words_from_scribe(scribe)
        self.assertEqual(len(words), 1)
        report = self.report(scribe)
        self.assertEqual(report["transcript"], scribe["text"])
        self.assertEqual(report["words"][0]["text"], "kept!")
        self.assertEqual(report["inputs"]["word_count"], 1)
        json.dumps(report, allow_nan=False)

    def test_unavailable_or_invalid_confidence_does_not_become_fake_certainty(self):
        scribe = {"text": "I agree.", "words": [
            {"text": "I", "start": 0, "end": 0.3, "logprob": 10000},
            {"text": "agree.", "start": 0.5, "end": 1, "logprob": True},
        ]}
        report = self.report(scribe)
        self.assertTrue(all(w["confidence"] is None for w in report["words"]))

    def test_invalid_confidence_does_not_poison_gradable_metrics(self):
        invalid = [10000, float("nan"), float("inf"), True, "bad", None]
        words = [{"text": "word", "start": i, "end": i + 0.5,
                  "logprob": invalid[i % len(invalid)]} for i in range(40)]
        words[0]["logprob"] = -0.2
        report = self.report({"text": " ".join(w["text"] for w in words), "words": words})
        self.assertTrue(report["gradable"])
        self.assertEqual(report["measurements"]["clarity_pct"], round(100 * math.exp(-0.2), 2))
        json.dumps(report, allow_nan=False)

    def test_monotone_ranges_use_scored_windows_skip_silence_and_clip_final_window(self):
        times = np.arange(3540) / 100
        voiced = ~((times >= 10) & (times < 20))
        f0 = np.full(times.size, 120.0)
        changing = (times >= 20) & (times < 30)
        f0[changing] = np.where(np.arange(changing.sum()) % 2, 80, 180)
        f0[~voiced] = np.nan
        track = VoiceTrack(times, f0, 12 * np.log2(f0 / 120), np.full(times.size, -20.0),
                           voiced, 120.0, 35.4)
        words = words_from_scribe(self.fx["scribe"])
        metrics, evidence = tone_metrics(words, track, self.lexicons, self.standards["analysis"])
        self.assertEqual(evidence["monotone_ranges"], [{"start": 0.0, "end": 10.0},
                                                       {"start": 30.0, "end": 35.4}])
        self.assertEqual(metrics["monotone_window_count"], len(evidence["monotone_ranges"]))
        self.assertEqual(len(evidence["window_pitch_variation_pct"]), 3)

    def test_monotone_ranges_follow_current_threshold_and_need_measurable_audio(self):
        words = words_from_scribe(self.fx["scribe"])
        metrics, evidence = tone_metrics(words, None, self.lexicons, self.standards["analysis"])
        self.assertIsNone(metrics["monotone_window_count"])
        self.assertEqual(evidence["monotone_ranges"], [])
        times = np.arange(200) / 100
        f0 = np.where(np.arange(200) % 2, 110.0, 130.0)
        track = VoiceTrack(times, f0, 12 * np.log2(f0 / 120), np.full(200, -20.0),
                           np.ones(200, dtype=bool), 120.0, 2.0)
        threshold = 100 * float(np.std(f0)) / float(np.mean(f0))
        analysis = {**self.standards["analysis"], "monotone_pvq_pct": threshold}
        _, evidence = tone_metrics(words, track, self.lexicons, analysis)
        self.assertEqual(evidence["monotone_ranges"], [])
        analysis["monotone_pvq_pct"] += 0.0001
        _, evidence = tone_metrics(words, track, self.lexicons, analysis)
        self.assertEqual(evidence["monotone_ranges"], [{"start": 0.0, "end": 2.0}])


if __name__ == "__main__":
    unittest.main()
