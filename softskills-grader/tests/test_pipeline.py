"""Checks for the grader. Run with:  python tests/test_pipeline.py   (or pytest)

Nothing here touches the network. The ElevenLabs and Gemini calls are replaced
with canned responses in the documented shape, so these tests prove our code and
our request format, not the live services.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures  # noqa: E402
from grader import audio, extract, grade, http, transcribe  # noqa: E402
from grader.config import StandardsError, load_standards, validate  # noqa: E402
from grader.features import compute_features, verify_extraction, words_from_scribe  # noqa: E402
from grader.scoring import bucket_score, score_all, score_factor  # noqa: E402

TMP = tempfile.mkdtemp(prefix="grader-tests-")
FX = fixtures.build(TMP)
CONFIG = str(ROOT / "config")
STANDARDS, LEXICONS, _ = load_standards(CONFIG)


def run(side="yes", **kwargs):
    kwargs.setdefault("transcript", FX["scribe"])
    kwargs.setdefault("extraction", FX["annotations"])
    kwargs.setdefault("stance_checks", FX["stance_checks"])
    kwargs.setdefault("cache_dir", os.path.join(TMP, "cache"))
    return grade(FX["audio"], FX["topic"], side, **kwargs)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self._payload


# ---------------------------------------------------------------- audio analysis
def test_pitch_tracker_matches_known_contour():
    track = audio.track_voice(audio.decode(FX["audio"]), 70, 400)
    assert abs(track.median_f0 - FX["truth"]["median_hz"]) / FX["truth"]["median_hz"] < 0.02
    measured = float(np.std(track.semitones[track.voiced]))
    assert abs(measured - FX["truth"]["pitch_sd_semitones"]) < 0.25, measured


def test_pitch_variation_quotient_matches_known_contour():
    measured = run()["measurements"]["pitch_variation_pct"]
    assert abs(measured - FX["truth"]["pvq_pct"]) < 0.5, (measured, FX["truth"]["pvq_pct"])


def test_uptalk_found_on_exactly_the_rising_sentences():
    report = run()
    found = report["evidence"]["tone"]["uptalk_sentences"]
    assert len(found) == FX["truth"]["uptalk_sentences"] == 2
    assert found[0]["text"].endswith("ordinary people.") and found[1]["text"].endswith("pace together.")


def test_speaking_rate_comes_from_word_timings():
    report = run()
    assert report["measurements"]["words_per_minute"] == round(FX["truth"]["wpm"], 2)
    assert report["measurements"]["long_pause_count"] == 1      # one silence over 2 s in the fixture


def test_decoding_without_ffmpeg():
    real_which, real_platform = audio.shutil.which, audio.sys.platform
    real_ffmpeg = real_which("ffmpeg")
    reference = audio.decode(FX["audio"])
    audio.shutil.which = lambda name: None                     # pretend nothing is installed
    try:
        # A WAV file still decodes, and to the same samples.
        assert np.allclose(audio.decode(FX["audio"]), reference, atol=1e-4)
        # Anything else fails with instructions instead of a cryptic parser error.
        fake_m4a = os.path.join(TMP, "speech.m4a")
        open(fake_m4a, "wb").write(b"\x00\x00\x00\x1cftypM4A " + b"\x00" * 64)
        try:
            audio.decode(fake_m4a)
        except RuntimeError as error:
            assert "ffmpeg" in str(error) and "conda install" in str(error)
        else:
            raise AssertionError("expected a RuntimeError")
        # On a Mac without ffmpeg, the built-in converter is used. Validate its
        # command, then use real ffmpeg as a stand-in on any host (including Windows).
        if real_ffmpeg:
            real_m4a = os.path.join(TMP, "real.m4a")
            subprocess.run([real_ffmpeg, "-v", "error", "-y", "-i", FX["audio"], "-c:a", "aac", real_m4a], check=True)
            audio.shutil.which = lambda name: "afconvert" if name == "afconvert" else None
            audio.sys.platform = "darwin"
            real_run = audio.subprocess.run
            def afconvert(cmd, **kwargs):
                assert cmd[:7] == ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1"]
                assert len(cmd) == 9
                return real_run([real_ffmpeg, "-v", "error", "-y", "-i", cmd[7],
                                 "-ac", "1", "-ar", "16000", cmd[8]], **kwargs)
            audio.subprocess.run = afconvert
            try:
                samples = audio.decode(real_m4a)
            finally:
                audio.subprocess.run = real_run
            assert abs(len(samples) - len(reference)) < 0.1 * audio.SAMPLE_RATE
            track = audio.track_voice(samples, 70, 400)
            assert abs(track.median_f0 - FX["truth"]["median_hz"]) / FX["truth"]["median_hz"] < 0.03
    finally:
        audio.shutil.which, audio.sys.platform = real_which, real_platform


# ----------------------------------------------------------------------- scoring
def test_bucket_edges_lower_inclusive_upper_exclusive():
    rule = STANDARDS["factors"]["pace"]["metrics"]["words_per_minute"]
    assert bucket_score(140, rule)[0] == 100
    assert bucket_score(139.99, rule)[0] == 85
    assert bucket_score(195, rule)[0] == 85
    assert bucket_score(194.99, rule)[0] == 100
    assert bucket_score(113, rule)[0] == 65
    assert bucket_score(112.99, rule)[0] == 40
    assert bucket_score(300, rule)[0] == 40
    pauses = STANDARDS["factors"]["pace"]["metrics"]["pause_time_pct"]
    assert bucket_score(55, pauses)[0] == 65     # falls through to "else"


def test_deduction_free_allowance_and_cap():
    factor = STANDARDS["factors"]["pace"]
    base = {"words_per_minute": 150, "pause_time_pct": 20, "time_used_pct": 95}
    assert score_factor(factor, {**base, "long_pause_count": 1})["deducted"] == 0     # first one is free
    assert score_factor(factor, {**base, "long_pause_count": 3})["deducted"] == 10    # 2 x 5
    assert score_factor(factor, {**base, "long_pause_count": 50})["deducted"] == 20   # capped
    assert score_factor(factor, {**base, "long_pause_count": 50})["score"] == 80.0


def test_overall_is_the_weighted_average_of_factor_scores():
    report = run()
    weights = {name: f["weight"] for name, f in STANDARDS["factors"].items()}
    expected = sum(weights[n] * report["factors"][n]["score"] for n in weights) / sum(weights.values())
    assert report["overall"]["score"] == round(expected, 1)
    for name, factor in report["factors"].items():
        rows = [r for r in factor["metrics"] if r["score"] is not None]
        base = sum(r["weight"] * r["score"] for r in rows) / sum(r["weight"] for r in rows)
        assert factor["base"] == round(base, 1), name
        lost = sum(d["points_lost"] for d in factor["deductions"])
        assert factor["score"] == round(max(0.0, min(100.0, base - lost)), 1), name


def test_unmeasurable_metric_is_left_out_and_the_rest_reweighted():
    factor = STANDARDS["factors"]["tone"]
    result = score_factor(factor, {"pitch_variation_pct": 25, "uptalk_pct": None,
                                   "hedges_per_100_words": 0, "monotone_window_count": 0})
    assert result["score"] == 100.0
    assert [r["score"] for r in result["metrics"] if r["metric"] == "uptalk_pct"] == [None]


# ------------------------------------------------------------------- determinism
def test_same_recording_gives_byte_identical_report():
    first = json.dumps(run(), sort_keys=True)
    second = json.dumps(run(), sort_keys=True)
    assert first == second


def test_identical_report_in_a_fresh_process_with_another_hash_seed():
    script = ("import json,sys;sys.path.insert(0,sys.argv[1]);from grader import grade;"
              "s=json.load(open(sys.argv[2]));a=json.load(open(sys.argv[3]));"
              "r=grade(sys.argv[4],sys.argv[5],'yes',transcript=s,"
              "extraction=json.loads(a['steps'][1]['content'][0]['text']),cache_dir=sys.argv[6],"
              "stance_checks=json.load(open(sys.argv[7])));"
              "print(json.dumps(r,sort_keys=True))")
    outputs = []
    for seed in ("1", "987"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        outputs.append(subprocess.run(
            [sys.executable, "-c", script, str(ROOT), os.path.join(TMP, "scribe.json"),
             os.path.join(TMP, "gemini.json"), FX["audio"], FX["topic"], os.path.join(TMP, "cache"),
             os.path.join(TMP, "stance.json")],
            capture_output=True, text=True, check=True, env=env).stdout)
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0]) == json.loads(json.dumps(run(), sort_keys=True))


def test_changing_the_standards_regrades_without_calling_any_api():
    cache = os.path.join(TMP, "cache-regrade")
    calls = []
    original = (http.requests.post, http.requests.post)

    def fake_post(url, **kwargs):
        calls.append(url)
        schema = (kwargs.get("json") or {}).get("response_format", {}).get("schema", {})
        if "evidence_quote" in schema.get("properties", {}):
            return FakeResponse({"output_text": json.dumps(fixtures.STANCE_CHECK)})
        return FakeResponse(FX["scribe"] if "elevenlabs" in url else FX["gemini_response"])

    os.environ["ELEVENLABS_API_KEY"], os.environ["GEMINI_API_KEY"] = "test-el", "test-gm"
    http.requests.post = http.requests.post = fake_post
    try:
        before = grade(FX["audio"], FX["topic"], "yes", cache_dir=cache)
        assert len(calls) == 3                       # transcription, extraction, focused stance
        again = grade(FX["audio"], FX["topic"], "yes", cache_dir=cache)
        assert len(calls) == 3 and again == before   # second grade is served from disk

        custom = os.path.join(TMP, "config-custom")
        shutil.copytree(CONFIG, custom, dirs_exist_ok=True)
        rubric = yaml.safe_load(open(os.path.join(custom, "standards.yaml")))
        rubric["factors"]["logic"]["weight"] = 60    # we decide logic matters more
        rubric["factors"]["pace"]["metrics"]["words_per_minute"]["buckets"] = [{"from": 180, "score": 100}]
        yaml.safe_dump(rubric, open(os.path.join(custom, "standards.yaml"), "w"))
        after = grade(FX["audio"], FX["topic"], "yes", cache_dir=cache, config_dir=custom)
        assert len(calls) == 3                       # still no new API calls
        assert after["measurements"] == before["measurements"]
        assert after["overall"]["score"] != before["overall"]["score"]
        assert after["inputs"]["standards_id"] != before["inputs"]["standards_id"]
    finally:
        http.requests.post, http.requests.post = original


# ---------------------------------------------------------------- Gemini grounding
def test_quotes_not_in_the_transcript_are_discarded():
    report = run()
    argument = report["evidence"]["argument"]
    discarded = [d["quote"] for d in argument["discarded_from_gemini"]]
    assert "AI development is moving too fast for regulators to keep up" in discarded
    assert "we must all set the pace together as one" in discarded
    assert report["measurements"]["supported_arguments"] == 3
    assert report["measurements"]["unsupported_claim_count"] == 1
    assert report["measurements"]["emotional_appeals"] == 3


def test_gemini_cannot_raise_the_grade_by_inventing_material():
    padded = copy.deepcopy(FX["annotations"])
    padded["arguments"] += [{"claim_quote": f"invented claim number {i} that nobody said",
                             "support_type": "statistic", "support_quote": "ninety percent of experts agree",
                             "supports_stance": True} for i in range(5)]
    padded["emotional_appeals"] += [{"type": "personal_story", "quote": "my grandmother lost everything"}]
    assert run(extraction=padded)["overall"] == run()["overall"]


def test_arguing_the_wrong_side_costs_the_configured_points():
    right, wrong = run(side="yes"), run(side="no")
    assert wrong["measurements"]["stance_mismatch"] == 1
    assert round(right["factors"]["logic"]["score"] - wrong["factors"]["logic"]["score"], 1) == 40.0


def test_research_based_orderings():
    logic = STANDARDS["factors"]["logic"]["metrics"]
    counter = {level: bucket_score(level, logic["counterargument_level"])[0] for level in (0, 1, 2)}
    assert counter[2] > counter[0] > counter[1]      # O'Keefe 1999: rebutted > one-sided > raised but unrebutted
    conclusion = {n: bucket_score(n, logic["structure_points"])[0] for n in (0, 1, 2)}
    assert conclusion[2] > conclusion[1] > conclusion[0]
    # Durik et al. 2008: colloquial hedges count, professional hedges do not.
    assert "kind of" in LEXICONS["hedges"] and "probably" not in LEXICONS["hedges"]


def test_malformed_gemini_output_does_not_crash():
    words = words_from_scribe(FX["scribe"])
    metrics, _ = verify_extraction({"stance": "banana", "arguments": ["x", None], "fallacies": None},
                                   words, LEXICONS, "yes")
    assert metrics["supported_arguments"] == 0 and metrics["stance_unclear"] == 1


# -------------------------------------------------------------------- API clients
def test_elevenlabs_request_shape_and_cache():
    seen, original = [], http.requests.post

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        seen.append({"url": url, "headers": headers, "data": data, "file": files["file"][0]})
        return FakeResponse(FX["scribe"])

    os.environ["ELEVENLABS_API_KEY"] = "test-el"
    http.requests.post = fake_post
    try:
        cache = os.path.join(TMP, "cache-el")
        cfg = STANDARDS["apis"]["elevenlabs"]
        first = transcribe.transcribe(FX["audio"], cfg, cache)
        second = transcribe.transcribe(FX["audio"], cfg, cache)
    finally:
        http.requests.post = original
    assert len(seen) == 1 and first == second == FX["scribe"]
    call = seen[0]
    assert call["url"] == "https://api.elevenlabs.io/v1/speech-to-text"
    assert call["headers"] == {"xi-api-key": "test-el"}
    assert call["data"] == {"model_id": "scribe_v2", "timestamps_granularity": "word",
                            "tag_audio_events": "false", "diarize": "false", "no_verbatim": "false",
                            "temperature": "0", "seed": "7", "language_code": "en"}


def test_gemini_request_shape_parse_and_cache():
    seen, original = [], http.requests.post

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append({"url": url, "headers": headers, "body": json})
        return FakeResponse(FX["gemini_response"])

    os.environ["GEMINI_API_KEY"] = "test-gm"
    http.requests.post = fake_post
    try:
        cache = os.path.join(TMP, "cache-gm")
        cfg = STANDARDS["apis"]["gemini"]
        first = extract.extract(FX["scribe"]["text"], FX["topic"], cfg, cache)
        second = extract.extract(FX["scribe"]["text"], FX["topic"], cfg, cache)
        extract.extract(FX["scribe"]["text"] + " One more sentence.", FX["topic"], cfg, cache)
    finally:
        http.requests.post = original
    assert len(seen) == 2 and first == second == FX["annotations"]   # new transcript -> new call
    body = seen[0]["body"]
    assert seen[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert seen[0]["headers"]["x-goog-api-key"] == "test-gm"
    assert body["model"] == "gemini-3.8-flash" and body["store"] is False
    assert body["generation_config"] == {"seed": 7, "thinking_level": "low"}
    assert body["response_format"]["mime_type"] == "application/json"
    assert body["response_format"]["schema"]["required"][0] == "stance"
    assert FX["scribe"]["text"] in body["input"] and FX["topic"] in body["input"]


def _gemini_with(statuses_by_model):
    """Run extract() against a fake Gemini that answers each model with the given statuses."""
    calls, sleeps = [], []
    original_post, original_sleep = http.requests.post, http.time.sleep

    def fake_post(url, headers=None, json=None, timeout=None):
        model = json["model"]
        calls.append(model)
        status = statuses_by_model[model].pop(0)
        return FakeResponse(FX["gemini_response"] if status == 200 else {"error": {"code": status}}, status)

    os.environ["GEMINI_API_KEY"] = "test-gm"
    http.requests.post, http.time.sleep = fake_post, sleeps.append
    cache = tempfile.mkdtemp(dir=TMP)
    try:
        result = extract.extract(FX["scribe"]["text"], FX["topic"], STANDARDS["apis"]["gemini"], cache)
        again = extract.extract(FX["scribe"]["text"], FX["topic"], STANDARDS["apis"]["gemini"], cache)
        assert again == result                      # cached, whichever model answered
        return result, calls, sleeps
    finally:
        http.requests.post, http.time.sleep = original_post, original_sleep


def test_busy_gemini_is_retried_then_succeeds():
    result, calls, sleeps = _gemini_with({"gemini-3.8-flash": [503, 503, 200]})
    assert result == FX["annotations"]
    assert calls == ["gemini-3.8-flash"] * 3 and sleeps == [2, 5]


def test_rate_limited_model_hands_over_at_once():
    result, calls, sleeps = _gemini_with({"gemini-3.8-flash": [429], "gemini-3.6-flash": [200]})
    assert result == FX["annotations"]
    assert calls == ["gemini-3.8-flash", "gemini-3.6-flash"] and sleeps == []


def test_overloaded_model_falls_back_to_the_next_one():
    result, calls, sleeps = _gemini_with({"gemini-3.8-flash": [503] * 4, "gemini-3.6-flash": [200]})
    assert result == FX["annotations"]
    assert calls == ["gemini-3.8-flash"] * 4 + ["gemini-3.6-flash"]


def test_every_model_failing_gives_one_readable_error():
    busy = {"gemini-3.8-flash": [503] * 4, "gemini-3.6-flash": [503] * 4, "gemini-3.5-flash-lite": [400]}
    try:
        _gemini_with(busy)
    except RuntimeError as error:
        text = str(error)
        assert all(name in text for name in busy), text
        assert busy["gemini-3.5-flash-lite"] == []   # a 400 is not retried
    else:
        raise AssertionError("expected a RuntimeError")


def test_rate_limited_key_hands_over_to_the_next_key():
    # Each key has its own quota: a spent key moves on to the next key on the same model.
    calls, original = [], http.requests.post

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((json["model"], headers["x-goog-api-key"]))
        spent = headers["x-goog-api-key"] in ("key-one", "key-two")
        return FakeResponse({"error": {"code": 429}}, 429) if spent else FakeResponse(FX["gemini_response"])

    os.environ["GEMINI_API_KEY"], os.environ["GEMINI_API_KEYS"] = "key-one", "key-two, key-three,key-one"
    http.requests.post = fake_post
    try:
        assert extract.api_keys() == ["key-one", "key-two", "key-three"]     # in order, no repeats
        result = extract.extract(FX["scribe"]["text"], FX["topic"], STANDARDS["apis"]["gemini"],
                                 tempfile.mkdtemp(dir=TMP))
        assert result == FX["annotations"]
        assert calls == [("gemini-3.8-flash", "key-one"), ("gemini-3.8-flash", "key-two"),
                         ("gemini-3.8-flash", "key-three")]
        os.environ["GEMINI_API_KEYS"] = "key-two"
        try:
            extract.extract(FX["scribe"]["text"], FX["topic"], STANDARDS["apis"]["gemini"], tempfile.mkdtemp(dir=TMP))
        except RuntimeError as error:
            assert "key 2" in str(error) and "key-" not in str(error), str(error)   # numbered, never shown
        else:
            raise AssertionError("expected a RuntimeError")
    finally:
        http.requests.post = original
        del os.environ["GEMINI_API_KEYS"]


# ---------------------------------------------------------------------- standards
def test_slow_gemini_model_is_raced_by_the_fallback():
    # The first model hangs; after HEDGE_AFTER the fallback starts and its answer wins.
    hang, original_post, original_hedge = threading.Event(), http.requests.post, extract.HEDGE_AFTER
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "gemini-3.8-flash":
            hang.wait(5)
        return FakeResponse(FX["gemini_response"])

    os.environ["GEMINI_API_KEY"] = "test-gm"
    http.requests.post, extract.HEDGE_AFTER = fake_post, 0.1
    try:
        started = time.monotonic()
        result = extract.extract(FX["scribe"]["text"], FX["topic"], STANDARDS["apis"]["gemini"],
                                 tempfile.mkdtemp(dir=TMP))
        elapsed = time.monotonic() - started
    finally:
        hang.set()
        http.requests.post, extract.HEDGE_AFTER = original_post, original_hedge
    assert result == FX["annotations"] and elapsed < 2, elapsed
    assert calls == ["gemini-3.8-flash", "gemini-3.6-flash"]


def test_slow_elevenlabs_is_sent_again_but_errors_are_not():
    original_post, original_hedge = http.requests.post, transcribe.HEDGE_AFTER
    hang, calls = threading.Event(), []

    def slow_then_fast(url, headers=None, data=None, files=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            hang.wait(5)
        return FakeResponse(FX["scribe"])

    def unauthorised(url, headers=None, data=None, files=None, timeout=None):
        calls.append(url)
        return FakeResponse({"detail": "invalid key"}, 401)

    os.environ["ELEVENLABS_API_KEY"] = "test-el"
    cfg = STANDARDS["apis"]["elevenlabs"]
    transcribe.HEDGE_AFTER = 0.1
    try:
        http.requests.post = slow_then_fast
        started = time.monotonic()
        result = transcribe.transcribe(FX["audio"], cfg, tempfile.mkdtemp(dir=TMP))
        assert result == FX["scribe"] and len(calls) == 2 and time.monotonic() - started < 2
        hang.set()

        calls.clear()
        http.requests.post = unauthorised
        try:
            transcribe.transcribe(FX["audio"], cfg, tempfile.mkdtemp(dir=TMP))
        except RuntimeError as error:
            assert "401" in str(error)
        else:
            raise AssertionError("expected a RuntimeError")
        assert len(calls) == 1                      # a 401 is not worth sending twice
    finally:
        hang.set()
        http.requests.post, transcribe.HEDGE_AFTER = original_post, original_hedge


def test_retries_stop_at_the_deadline():
    calls, sleeps = [], []
    original_post, original_sleep = http.requests.post, http.time.sleep

    def busy(url, **kwargs):
        calls.append(kwargs.get("timeout"))
        return FakeResponse({}, 503)

    http.requests.post, http.time.sleep = busy, sleeps.append
    try:
        response = http.post_with_retry("https://example.test", deadline=3)
    finally:
        http.requests.post, http.time.sleep = original_post, original_sleep
    assert response.status_code == 503
    assert sleeps == [2] and calls == [http.TIMEOUT, http.TIMEOUT]   # the 5 s wait would pass the deadline


# -------------------------------------------------------------- critical failures
def _strong_features():
    """A speech that is excellent everywhere: no critical rule should fire."""
    return dict(copy.deepcopy(run()["measurements"]))


def test_a_strong_speech_is_not_capped():
    report = score_all(_strong_features(), STANDARDS)
    assert report["overall"]["capped_by"] == []
    assert report["overall"]["score"] == report["overall"]["uncapped"]


def test_off_topic_speech_gets_a_very_low_grade_despite_great_delivery():
    features = _strong_features()
    features["off_topic"] = 1
    report = score_all(features, STANDARDS)
    assert report["overall"]["uncapped"] > 80                  # delivery alone would still pass
    assert report["overall"]["score"] == 25 and report["overall"]["letter"] == "F"
    assert report["overall"]["capped_by"][0]["metric"] == "off_topic"


def test_each_part_has_a_failure_that_sinks_the_grade():
    cases = {"words_per_minute": 80, "time_used_pct": 30, "vocabulary_variety_pct": 45,
             "fillers_per_100_words": 16, "pitch_variation_pct": 8, "hedges_per_100_words": 5,
             "stance_mismatch": 1, "supported_arguments": 0, "manipulative_appeal_count": 2}
    for metric, value in cases.items():
        features = _strong_features()
        features[metric] = value
        report = score_all(features, STANDARDS)
        assert report["overall"]["score"] <= 60, (metric, report["overall"])
        assert metric in [row["metric"] for row in report["overall"]["capped_by"]], metric


def test_weak_logic_caps_the_grade_even_when_no_single_rule_fires():
    features = _strong_features()
    features.update(supported_arguments=1, structure_points=0, counterargument_level=1,
                    connectives_per_100_words=0, unsupported_claim_count=4, reasoning_fallacy_count=2)
    report = score_all(features, STANDARDS)
    assert report["factors"]["logic"]["score"] < 50
    assert report["overall"]["score"] <= 60
    assert report["overall"]["capped_by"][0]["metric"] == "logic_score"


def test_the_lowest_cap_wins_and_every_failure_is_listed():
    features = _strong_features()
    features.update(off_topic=1, words_per_minute=80)
    capped = score_all(features, STANDARDS)["overall"]
    assert capped["score"] == 25
    assert [row["cap"] for row in capped["capped_by"]] == sorted(row["cap"] for row in capped["capped_by"])
    assert {"off_topic", "words_per_minute"} <= {row["metric"] for row in capped["capped_by"]}


def test_on_topic_label_becomes_metrics_and_missing_label_is_not_punished():
    words = words_from_scribe(FX["scribe"])
    for label, off, drift in (("no", 1, 0), ("partly", 0, 1), ("yes", 0, 0), (None, 0, 0), ("banana", 0, 0)):
        extraction = dict(FX["annotations"], on_topic=label)
        metrics, evidence = verify_extraction(extraction, words, LEXICONS, "yes")
        assert (metrics["off_topic"], metrics["topic_drift"]) == (off, drift), label


def test_bad_critical_rules_are_rejected():
    bad_rules = [
        {"metric": "words_per_minute", "cap": 50, "reason": "x"},                          # no test
        {"metric": "words_per_minute", "below": 90, "at_least": 1, "cap": 50, "reason": "x"},
        {"metric": "nonsense", "below": 90, "cap": 50, "reason": "x"},
        {"metric": "words_per_minute", "below": 90, "cap": 150, "reason": "x"},
        {"metric": "words_per_minute", "below": 90, "cap": 50},                           # no reason
        {"metric": "words_per_minute", "score_below": 50, "cap": 50, "reason": "x"},
    ]
    for rule in bad_rules:
        broken = copy.deepcopy(STANDARDS)
        broken["factors"]["pace"]["critical"] = [rule]
        try:
            validate(broken, LEXICONS)
        except StandardsError:
            continue
        raise AssertionError(f"accepted a bad rule: {rule}")


def test_bad_standards_are_rejected_with_a_clear_message():
    overlapping = copy.deepcopy(STANDARDS)
    overlapping["factors"]["pace"]["metrics"]["words_per_minute"]["buckets"] = [
        {"from": 100, "to": 150, "score": 100}, {"from": 140, "to": 180, "score": 80}]
    unknown = copy.deepcopy(STANDARDS)
    unknown["factors"]["pace"]["metrics"]["charisma"] = {"weight": 10, "buckets": [{"from": 0, "score": 100}]}
    for broken, phrase in ((overlapping, "overlap"), (unknown, "unknown metric")):
        try:
            validate(broken, LEXICONS)
        except StandardsError as error:
            assert phrase in str(error), error
        else:
            raise AssertionError(f"expected a StandardsError mentioning {phrase!r}")


def test_removing_a_factor_rebalances_the_rest():
    trimmed = copy.deepcopy(STANDARDS)
    del trimmed["factors"]["emotion"]
    validate(trimmed, LEXICONS)
    report = run()
    rescored = score_all(report["measurements"], trimmed)
    assert set(rescored["factors"]) == {"pace", "diction", "tone", "logic"}
    assert abs(sum(f["weight_pct"] for f in rescored["factors"].values()) - 100.0) <= 0.2   # rounding


def test_too_little_speech_is_not_graded():
    short = {"text": "Um, I agree.", "words": FX["scribe"]["words"][:6]}
    report = run(transcript=short)
    assert report["gradable"] is False and report["overall"]["score"] == 0.0 and "words" in report["reason"]


def test_transcript_without_punctuation_still_grades():
    bare = copy.deepcopy(FX["scribe"])
    for item in bare["words"]:
        item["text"] = item["text"].rstrip(".,!?")
    report = run(transcript=bare)
    assert report["measurements"]["uptalk_pct"] is None
    assert report["factors"]["tone"]["score"] is not None


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as error:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(error).__name__}: {error}")
    report = run()
    digest = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()[:16]
    print(f"\n{len(tests) - failed}/{len(tests)} passed   sample overall {report['overall']}   report hash {digest}")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
