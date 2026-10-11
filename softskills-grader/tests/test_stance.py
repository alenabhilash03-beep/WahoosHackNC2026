"""Focused stance verification and scoring regressions; all provider calls are mocked."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures
from grader import grade, http
from grader.config import StandardsError, load_standards, validate
from grader.stance import DEFAULTS, SCHEMA, SYSTEM, build_request, classify, grounded_check, verify_stance

TOPIC = "Should homework be banned?"
SPEECH = "Some people want homework banned, but I disagree. Homework should not be banned. Regular practice helps students learn."
QUOTE = "Homework should not be banned."


def answer(side="no", quote=QUOTE):
    return {"stance": side, "position": "Keep homework.", "evidence_quote": quote,
            "explanation": "The speaker opposes banning homework."}


class Response:
    status_code = 200
    text = ""

    def __init__(self, raw):
        self.raw = raw

    def json(self):
        return {"output_text": json.dumps(self.raw)}


class StanceTests(unittest.TestCase):
    def verify(self, chosen="no", primary=None, confirmation=None, transcript=SPEECH):
        return verify_stance(transcript, TOPIC, chosen, {}, "unused",
                             {"primary": primary if primary is not None else answer(),
                              "confirmation": confirmation})

    def test_both_chosen_sides_can_match_without_confirmation(self):
        for side in ("yes", "no"):
            with self.subTest(side=side):
                result = self.verify(chosen=side, primary=answer(side))
                self.assertEqual(result["result"], "matches")
                self.assertEqual(result["stance"], side)
                self.assertIsNone(result["confirmation"])

    def test_real_matching_no_position_replaces_a_broad_extractor_mismatch(self):
        result = self.verify()
        self.assertEqual(result["result"], "matches")
        self.assertEqual(result["primary"]["evidence_quote"], QUOTE)

    def test_wrong_side_requires_agreement_for_both_selected_sides(self):
        for chosen, argued in (("yes", "no"), ("no", "yes")):
            with self.subTest(chosen=chosen):
                result = self.verify(chosen, answer(argued), answer(argued))
                self.assertEqual(result["result"], "contradicts")
                self.assertEqual(result["status"], "confirmed")
                self.assertTrue(result["confirmation"]["quote_verified"])

    def test_disagreement_withholds_the_penalty(self):
        result = self.verify("yes", answer("no"), answer("yes"))
        self.assertEqual(result["result"], "unclear")
        self.assertEqual(result["status"], "disputed")
        self.assertEqual(result["stance"], "unclear")

    def test_ambiguous_primary_and_confirmation_withhold_the_penalty(self):
        for primary, confirmation in ((answer("unclear", ""), None),
                                      (answer("no"), answer("unclear", ""))):
            result = self.verify("yes", primary, confirmation)
            self.assertEqual(result["result"], "unclear")
            self.assertEqual(result["status"], "unclear")

    def test_missing_confirmation_does_not_accept_a_single_mismatch(self):
        self.assertEqual(self.verify("yes")["result"], "unclear")

    def test_invented_or_stitched_quotes_do_not_allow_a_mismatch(self):
        for quote in ("Homework should be banned.", "I disagree. Regular practice helps students learn.",
                      "Yes", "", "homework", "Homework should not be banned. " * 20):
            with self.subTest(quote=quote):
                result = self.verify("yes", answer("no", quote), answer("no"))
                self.assertEqual(result["result"], "unclear")
                self.assertEqual(result["status"], "unverified")
                self.assertEqual(result["primary"]["evidence_quote"], "")

    def test_confirmation_quote_is_also_required(self):
        result = self.verify("yes", answer("no"), answer("no", "Nobody said these words."))
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["result"], "unclear")

    def test_quote_matching_preserves_negation_and_word_boundaries(self):
        self.assertFalse(grounded_check(answer(quote="Homework should be banned."), SPEECH, "model")["quote_verified"])
        self.assertFalse(grounded_check(answer(quote="work should not be banned"), SPEECH, "model")["quote_verified"])
        self.assertTrue(grounded_check(answer(quote="homework should not be banned"), SPEECH, "model")["quote_verified"])

    def test_malformed_provider_fields_are_not_trusted(self):
        for raw in (None, [], "yes", {"stance": "banana"}, {"stance": True},
                    {"stance": "no", "evidence_quote": [], "quote_verified": True}):
            with self.subTest(raw=raw):
                result = self.verify("yes", raw, answer()) if raw is not None else self.verify("yes", {}, answer())
                self.assertEqual(result["result"], "unclear")

    def test_prompt_handles_bans_negation_and_quoted_opposition(self):
        request = build_request(SPEECH, TOPIC, DEFAULTS["model"], "minimal")
        self.assertEqual(request["model"], "gemini-3.5-flash-lite")
        self.assertEqual(request["generation_config"], {"thinking_level": "minimal"})
        self.assertEqual(request["response_format"]["schema"], SCHEMA)
        self.assertFalse(request["store"])
        self.assertIn(TOPIC, request["input"])
        self.assertIn(SPEECH, request["input"])
        self.assertIn("'keep homework' is No", SYSTEM)
        self.assertIn("quoted and then rebutted", SYSTEM)
        self.assertIn("unresolved contradictory positions", SYSTEM)
        self.assertNotIn("Selected side:", request["input"])
        self.assertNotIn("temperature", request["generation_config"])

    def test_live_match_uses_one_focused_call(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("grader.stance.classify", return_value=grounded_check(answer(), SPEECH, DEFAULTS["model"])) as api:
            result = verify_stance(SPEECH, TOPIC, "no", {}, folder)
        self.assertEqual(result["result"], "matches")
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.args[2:4], ("gemini-3.5-flash-lite", "minimal"))

    def test_live_mismatch_uses_an_independent_stronger_confirmation(self):
        calls = []
        def fake_api(transcript, topic, model, level, folder):
            calls.append((transcript, topic, model, level))
            return grounded_check(answer(), transcript, model)
        with patch("grader.stance.classify", side_effect=fake_api):
            result = verify_stance(SPEECH, TOPIC, "yes", {}, "unused")
        self.assertEqual(result["result"], "contradicts")
        self.assertEqual(calls, [(SPEECH, TOPIC, "gemini-3.5-flash-lite", "minimal"),
                                (SPEECH, TOPIC, "gemini-3.8-flash", "medium")])

    def test_primary_and_confirmation_failures_are_safe_and_sanitized(self):
        for responses in ([RuntimeError("secret-key")],
                          [grounded_check(answer(), SPEECH, DEFAULTS["model"]), RuntimeError("secret-key")]):
            with patch("grader.stance.classify", side_effect=responses):
                result = verify_stance(SPEECH, TOPIC, "yes", {}, "unused")
            self.assertEqual(result["result"], "unclear")
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("secret-key", json.dumps(result))

    def test_requests_are_cached_by_topic_model_thinking_and_prompt_version(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}, clear=True), \
                patch.object(http.requests, "post", return_value=Response(answer())) as post:
            first = classify(SPEECH, TOPIC, DEFAULTS["model"], "minimal", folder)
            self.assertEqual(classify(SPEECH, TOPIC, DEFAULTS["model"], "minimal", folder), first)
            self.assertEqual(post.call_count, 1)
            classify(SPEECH, "Should schools keep homework?", DEFAULTS["model"], "minimal", folder)
            classify(SPEECH, TOPIC, "gemini-3.8-flash", "medium", folder)
            classify(SPEECH, TOPIC, DEFAULTS["model"], "medium", folder)
            classify(SPEECH + " More context.", TOPIC, DEFAULTS["model"], "minimal", folder)
            with patch("grader.stance.PROMPT_VERSION", "new-prompt"):
                classify(SPEECH, TOPIC, DEFAULTS["model"], "minimal", folder)
            self.assertEqual(post.call_count, 6)
            request = post.call_args.kwargs["json"]
            self.assertIn("evidence_quote", request["response_format"]["schema"]["properties"])
            self.assertEqual(post.call_args.kwargs["timeout"], (3, 12))

    def test_cached_stance_is_compared_against_each_selected_side(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}, clear=True), \
                patch.object(http.requests, "post", return_value=Response(answer())) as post:
            self.assertEqual(verify_stance(SPEECH, TOPIC, "no", {}, folder)["result"], "matches")
            self.assertEqual(post.call_count, 1)
            self.assertEqual(verify_stance(SPEECH, TOPIC, "yes", {}, folder)["result"], "contradicts")
            self.assertEqual(post.call_count, 2)  # cached primary, new confirmation
            verify_stance(SPEECH, TOPIC, "yes", {}, folder)
            self.assertEqual(post.call_count, 2)

    def test_failed_api_result_is_not_cached_and_a_retry_can_recover(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}, clear=True):
            with patch.object(http.requests, "post", side_effect=RuntimeError("offline")):
                self.assertEqual(verify_stance(SPEECH, TOPIC, "no", {}, folder)["status"], "unavailable")
            with patch.object(http.requests, "post", return_value=Response(answer())) as post:
                self.assertEqual(verify_stance(SPEECH, TOPIC, "no", {}, folder)["result"], "matches")
                self.assertEqual(post.call_count, 1)

    def test_second_configured_api_key_can_recover_a_rate_limit(self):
        busy = Response({})
        busy.status_code = 429
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {"GEMINI_API_KEY": "first", "GEMINI_API_KEYS": "second"}, clear=True), \
                patch.object(http.requests, "post", side_effect=[busy, Response(answer())]) as post:
            self.assertEqual(verify_stance(SPEECH, TOPIC, "no", {}, folder)["result"], "matches")
        self.assertEqual([call.kwargs["headers"]["x-goog-api-key"] for call in post.call_args_list], ["first", "second"])

    def test_corrupt_cache_is_rebuilt_and_missing_credentials_are_safe(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}, clear=True), \
                patch.object(http.requests, "post", return_value=Response(answer())) as post:
            classify(SPEECH, TOPIC, DEFAULTS["model"], "minimal", folder)
            next((Path(folder) / "stances").glob("*.json")).write_text("broken json")
            classify(SPEECH, TOPIC, DEFAULTS["model"], "minimal", folder)
            self.assertEqual(post.call_count, 2)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(verify_stance(SPEECH, TOPIC, "no", {}, "unused")["status"], "unavailable")

    def test_malformed_json_or_payload_withholds_the_penalty(self):
        for payload in ({"output_text": "not json"}, None, {"output_text": "[]"},
                        {"steps": [None]}, {"steps": [{"type": "model_output", "content": [None]}]}):
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}, clear=True), \
                    patch.object(http.requests, "post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = payload
                self.assertEqual(verify_stance(SPEECH, TOPIC, "yes", {}, folder)["result"], "unclear")

    def test_stance_configuration_rejects_typos_and_empty_models(self):
        standards, lexicons, _ = load_standards(str(ROOT / "config"))
        for invalid in ("bad", {"modle": "x"}, {"model": ""}, {"thinking_level": "banana"}):
            changed = copy.deepcopy(standards)
            changed["apis"]["stance_checker"] = invalid
            with self.assertRaises(StandardsError):
                validate(changed, lexicons)


class PipelineStanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.fx = fixtures.build(cls.folder.name)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def report(self, side="yes", annotations=None, checks=None):
        return grade(self.fx["audio"], self.fx["topic"], side, transcript=self.fx["scribe"],
                     extraction=annotations or self.fx["annotations"], cache_dir=self.folder.name,
                     stance_checks=checks if checks is not None else self.fx["stance_checks"])

    def test_focused_check_overrides_a_false_broad_extractor_mismatch(self):
        wrong = {**self.fx["annotations"], "stance": "no"}
        report = self.report(annotations=wrong)
        self.assertEqual(report["measurements"]["stance_mismatch"], 0)
        self.assertEqual(report["evidence"]["argument"]["extracted_stance"], "no")
        self.assertEqual(report["evidence"]["argument"]["stance_argued"], "yes")
        self.assertFalse(any(cap["metric"] == "stance_mismatch" for cap in report["overall"]["capped_by"]))

    def test_confirmed_mismatch_keeps_the_existing_deduction_and_overall_cap(self):
        report = self.report(side="no")
        self.assertEqual(report["measurements"]["stance_mismatch"], 1)
        deduction = next(row for row in report["factors"]["logic"]["deductions"] if row["metric"] == "stance_mismatch")
        self.assertEqual(deduction["points_lost"], 40)
        self.assertLessEqual(report["overall"]["score"], 40)
        self.assertEqual(report["evidence"]["argument"]["stance_verification"]["status"], "confirmed")

    def test_unverified_stance_does_not_trigger_either_side_penalty(self):
        report = self.report(side="no", checks={"primary": fixtures.STANCE_CHECK, "confirmation": {}})
        self.assertTrue(report["gradable"])
        self.assertIsNone(report["measurements"]["stance_mismatch"])
        self.assertIsNone(report["measurements"]["stance_unclear"])
        self.assertFalse(any(row["metric"] in ("stance_mismatch", "stance_unclear") for row in report["factors"]["logic"]["deductions"]))
        self.assertGreater(report["overall"]["score"], 40)

    def test_provider_outage_preserves_the_rest_of_the_grade(self):
        with patch("grader.stance.classify", side_effect=RuntimeError("offline")):
            report = grade(self.fx["audio"], self.fx["topic"], "no", transcript=self.fx["scribe"],
                           extraction=self.fx["annotations"], cache_dir=self.folder.name)
        self.assertTrue(report["gradable"])
        self.assertIsNone(report["measurements"]["stance_mismatch"])
        self.assertEqual(report["evidence"]["argument"]["stance_verification"]["status"], "unavailable")

    def test_too_short_speech_never_calls_the_stance_service(self):
        short = {"text": "I agree.", "words": self.fx["scribe"]["words"][:5]}
        with patch("grader.pipeline.verify_stance") as api:
            report = grade(self.fx["audio"], self.fx["topic"], "yes", transcript=short, cache_dir=self.folder.name)
        self.assertFalse(report["gradable"])
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
