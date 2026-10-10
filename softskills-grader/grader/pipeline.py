"""The whole pipeline in one call:  grade(audio, topic, side) -> report.

  1. ElevenLabs Scribe   what was said, and exactly when          (cached per recording)
  2. our audio analysis  pitch and loudness from the raw sound
  3. Gemini              the structure of the argument, as quotes  (cached per transcript)
  4. our feature code    verify Gemini's quotes, count, measure
  5. our scoring engine  buckets, deductions, weights from config/standards.yaml

Steps 4 and 5 are pure code. Steps 1 and 3 run once per recording and are frozen
on disk, so grading the same recording again always returns the same report, and
changing the standards re-grades old recordings without calling any API.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import audio
from .config import load_standards
from .extract import extract
from .features import compute_features, words_from_scribe
from .scoring import score_all
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
          extraction: dict | None = None) -> dict:
    """Grade one spoken argument.

    side        "yes" or "no": the side the player picked.
    transcript  optional ready-made ElevenLabs response (skips step 1).
    extraction  optional ready-made Gemini annotations (skips step 3).
    """
    side = side.strip().lower()
    if side not in ("yes", "no"):
        raise ValueError("side must be 'yes' or 'no'")
    config_dir = config_dir or str(ROOT / "config")
    cache_dir = cache_dir or str(ROOT / "cache")
    load_env()
    standards, lexicons, standards_id = load_standards(config_dir)

    audio_sha = sha256_file(audio_path)
    scribe = transcript or transcribe(audio_path, standards["apis"]["elevenlabs"], cache_dir, audio_sha)
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

    analysis = standards["analysis"]
    track = audio.track_voice(audio.decode(audio_path), analysis["pitch_floor_hz"], analysis["pitch_ceiling_hz"])
    annotations = extraction or extract(text, topic, standards["apis"]["gemini"], cache_dir)
    features, evidence = compute_features(scribe, track, annotations, lexicons, standards, side)

    report.update(score_all(features, standards))
    report["measurements"], report["evidence"] = features, evidence
    return report
