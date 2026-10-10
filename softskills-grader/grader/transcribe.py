"""ElevenLabs Scribe: speech -> words with start/end times and confidence.

ElevenLabs gives us the raw material (what was said and exactly when). It never
scores anything. Results are cached by the hash of the audio, so a recording is
transcribed once and every later grade of it reuses the same transcript.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import requests

URL = "https://api.elevenlabs.io/v1/speech-to-text"


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def transcribe(audio_path: str, cfg: dict, cache_dir: str, audio_sha: str | None = None) -> dict:
    audio_sha = audio_sha or sha256_file(audio_path)
    key = hashlib.sha256(json.dumps([audio_sha, cfg], sort_keys=True).encode()).hexdigest()[:32]
    cache_file = Path(cache_dir) / "transcripts" / f"{key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    form = {
        "model_id": cfg.get("model_id", "scribe_v2"),
        "timestamps_granularity": "word",
        "tag_audio_events": "false",
        "diarize": "false",
        "no_verbatim": "false",      # keep "um", "uh" and false starts: we count them
        "temperature": "0",
        "seed": str(cfg.get("seed", 7)),
    }
    if cfg.get("language_code"):
        form["language_code"] = cfg["language_code"]

    with open(audio_path, "rb") as handle:
        response = requests.post(
            URL,
            headers={"xi-api-key": api_key},
            data=form,
            files={"file": (os.path.basename(audio_path), handle)},
            timeout=180,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"ElevenLabs returned {response.status_code}: {response.text[:400]}")
    result = response.json()
    if "words" not in result:
        raise RuntimeError(f"ElevenLabs response has no 'words': {str(result)[:400]}")

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result))
    return result
