"""The whole pipeline in one call:  grade(audio, topic, side) -> report.

  1. ElevenLabs Scribe   what was said, and exactly when          (cached per recording)
  2. our audio analysis  pitch and loudness from the raw sound
  3. Gemini              the structure of the argument, as quotes  (cached per transcript)
  4. Flash-Lite          a focused stance check; Flash confirms apparent mismatches
  5. our feature code    verify quotes, count, measure
  6. our scoring engine  buckets, deductions, weights from config/standards.yaml

Steps 5 and 6 are pure code. Successful provider results are cached on disk, so
repeat grades reuse the same annotations. Failed stance checks can be retried;
changing scoring weights reuses the provider cache.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import audio
from .config import load_standards
from .extract import extract
from .features import compute_features, words_from_scribe
from .scoring import score_all
from .stance import apply_verification, verify_stance
from .transcribe import sha256_file, transcribe

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def grade(audio_path: str, topic: str, side: str, config_dir: str | None = None,
          cache_dir: str | None = None, transcript: dict | None = None,
          extraction: dict | None = None, stance_checks: dict | None = None) -> dict:
    """Grade one spoken argument.

    side        "yes" or "no": the side the player picked.
    transcript  optional ready-made ElevenLabs response (skips step 1).
    extraction  optional ready-made Gemini annotations (skips step 3).
    stance_checks optional raw primary/confirmation responses (skips stance API calls).
    """
    side = side.strip().lower()
    if side not in ("yes", "no"):
        raise ValueError("side must be 'yes' or 'no'")
    config_dir = config_dir or str(ROOT / "config")
    cache_dir = cache_dir or str(ROOT / "cache")
    load_env()
    standards, lexicons, standards_id = load_standards(config_dir)

    audio_sha = sha256_file(audio_path)
    # Our own pitch and loudness analysis needs only the audio, so it runs while
    # ElevenLabs transcribes.
    analysis = standards["analysis"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        voice = pool.submit(lambda: audio.track_voice(audio.decode(audio_path),
                                                      analysis["pitch_floor_hz"], analysis["pitch_ceiling_hz"]))
        scribe = transcript or transcribe(audio_path, standards["apis"]["elevenlabs"], cache_dir, audio_sha)
        track = voice.result()
    words = words_from_scribe(scribe)
    text = scribe.get("text") or " ".join(w["raw"] for w in words)

    report = {
        "gradable": True, "reason": None, "overall": None, "factors": {},
        "inputs": {"topic": topic, "side": side, "audio_sha256": audio_sha,
                   "standards_version": standards.get("version"), "standards_id": standards_id,
                   "word_count": len(words)},
        "measurements": {}, "evidence": {}, "transcript": text,
        "words": [{"text": w["raw"], "start": w["start"], "end": w["end"],
                   "confidence": w["confidence"]} for w in words],
    }
    minimum = standards["session"]["min_words_to_grade"]
    if len(words) < minimum:
        report.update(gradable=False, overall={"score": 0.0, "letter": standards["grades"][-1]["letter"]},
                      reason=f"Only {len(words)} words were spoken; at least {minimum} are needed.")
        return report

    # Independent stance verification can run alongside the broad annotation call.
    with ThreadPoolExecutor(max_workers=1) as pool:
        stance = pool.submit(verify_stance, text, topic, side,
                            standards["apis"].get("stance_checker", {}), cache_dir, stance_checks)
        annotations = extraction or extract(text, topic, standards["apis"]["gemini"], cache_dir)
        verification = stance.result()
    features, evidence = compute_features(scribe, track, annotations, lexicons, standards, side)
    apply_verification(features, evidence, verification)

    report.update(score_all(features, standards))
    report["measurements"], report["evidence"] = features, evidence
    return report
