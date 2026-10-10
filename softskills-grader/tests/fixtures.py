"""Synthetic test material with KNOWN ground truth.

build(folder) writes:
  speech.wav    a buzzy synthetic "voice" (not real speech) that follows the word
                timings, with a pitch contour we control, so the pitch tracker and
                uptalk detector can be checked against the truth
  scribe.json   a response in the exact shape ElevenLabs Scribe returns
  gemini.json   a response in the exact shape the Gemini Interactions API returns,
                including two items with quotes that are NOT in the transcript
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile

SR = 16000
BASE_HZ = 120.0
TOPIC = "Should the frontier of AI be paced?"

# (sentence, ends on a rising pitch?, extra silence after it in seconds)
SPEECH = [
    ("I believe the frontier of AI should be paced.", False, 0.0),
    ("First, safety research needs time to catch up, because right now we can build systems faster than we can test them.", False, 0.0),
    ("For example, um, labs have released models and only later discovered serious flaws.", False, 0.0),
    ("Second, pacing protects ordinary people.", True, 1.7),
    ("Imagine a nurse or a truck driver who wakes up one morning and finds that their job has disappeared overnight.", False, 0.0),
    ("Our families deserve time to adapt.", False, 0.0),
    ("Third, trust matters.", False, 0.0),
    ("A recent survey found that most people, uh, do not trust companies to regulate themselves, which means moving slower is how we earn that trust.", False, 1.1),
    ("Some people argue that slowing down lets our rivals win the race.", False, 0.0),
    ("However, a race with no brakes does not have a winner, it just has a crash.", False, 0.0),
    ("So, I think we should, you know, set the pace together.", True, 0.0),
    ("In conclusion, we should pace the frontier, because the future is worth getting right.", False, 0.0),
]

GEMINI_ANNOTATIONS = {
    "stance": "yes",
    "thesis_quote": "I believe the frontier of AI should be paced.",
    "conclusion_quote": "we should pace the frontier, because the future is worth getting right",
    "arguments": [
        {"claim_quote": "safety research needs time to catch up", "support_type": "reasoning",
         "support_quote": "because right now we can build systems faster than we can test them",
         "supports_stance": True},
        {"claim_quote": "pacing protects ordinary people", "support_type": "example",
         "support_quote": "Imagine a nurse or a truck driver who wakes up one morning and finds that their job has disappeared overnight",
         "supports_stance": True},
        # Gemini tidied away the "uh": our matcher ignores fillers on both sides.
        {"claim_quote": "Third, trust matters", "support_type": "statistic",
         "support_quote": "A recent survey found that most people do not trust companies to regulate themselves",
         "supports_stance": True},
        {"claim_quote": "Our families deserve time to adapt", "support_type": "none",
         "support_quote": "", "supports_stance": True},
        # Not in the transcript: must be discarded.
        {"claim_quote": "AI development is moving too fast for regulators to keep up", "support_type": "reasoning",
         "support_quote": "because right now we can build systems faster than we can test them",
         "supports_stance": True},
    ],
    "counterargument_quote": "Some people argue that slowing down lets our rivals win the race",
    "rebuttal_quote": "a race with no brakes does not have a winner, it just has a crash",
    "fallacies": [],
    "emotional_appeals": [
        {"type": "human_stakes", "quote": "Imagine a nurse or a truck driver who wakes up one morning"},
        {"type": "shared_values", "quote": "Our families deserve time to adapt"},
        {"type": "vivid_imagery", "quote": "a race with no brakes does not have a winner"},
        # Paraphrase, not a quote: must be discarded.
        {"type": "call_to_action", "quote": "we must all set the pace together as one"},
    ],
}


def _timeline():
    """Deterministic word timings: longer words take longer, punctuation adds a pause."""
    words, t = [], 0.4
    for s_index, (sentence, rise, extra) in enumerate(SPEECH):
        parts = sentence.split()
        for w_index, raw in enumerate(parts):
            letters = sum(ch.isalpha() for ch in raw)
            duration = min(0.50, max(0.12, 0.06 + 0.046 * letters))
            filler = raw.strip(",.").lower() in ("um", "uh")
            if filler:
                t += 0.20
            words.append({"raw": raw, "start": round(t, 3), "end": round(t + duration, 3),
                          "sentence": s_index, "last": w_index == len(parts) - 1,
                          "logprob": -0.35 if filler else round(-0.02 - 0.03 * ((len(words) * 7) % 5) / 5, 4)})
            t += duration
            t += 0.50 if raw[-1] in ".!?" else 0.22 if raw[-1] == "," else 0.04
        t += extra
    return words, t + 0.4


def _synthesise(words, total: float):
    n = int(total * SR)
    time = np.arange(n) / SR
    semis = np.zeros(n)
    amp = np.zeros(n)
    for s_index, (_, rise, _) in enumerate(SPEECH):
        mine = [w for w in words if w["sentence"] == s_index]
        s0, s1 = mine[0]["start"], mine[-1]["end"]
        span = (time >= s0) & (time <= s1)
        position = (time[span] - s0) / (s1 - s0)
        contour = 3.5 - 7.0 * position                      # falls 7 semitones across a sentence
        if rise:                                            # ... unless it ends on a rise
            tail = time[span] > s1 - 0.6
            lift = (time[span][tail] - (s1 - 0.6)) / 0.6
            contour[tail] = contour[tail][0] + 7.0 * lift
        semis[span] = contour
    for i, w in enumerate(words):
        a, b = int(w["start"] * SR), int(w["end"] * SR)
        level = 0.25 * 10 ** (3.0 * np.sin(1.7 * i) / 20)   # +-3 dB from word to word
        envelope = np.ones(b - a)
        edge = int(0.015 * SR)
        ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(edge) / edge)
        envelope[:edge], envelope[-edge:] = ramp, ramp[::-1]
        amp[a:b] = level * envelope
    f0 = BASE_HZ * 2 ** (semis / 12)
    phase = 2 * np.pi * np.cumsum(f0) / SR
    voice = sum(np.sin(k * phase) / k for k in range(1, 9))
    signal = amp * voice / 2.0 + np.random.default_rng(0).normal(0, 0.0005, n)
    voiced = amp > 0.01
    quotients = []
    for start in range(0, n, 10 * SR):                       # same 10 s windows as the grader
        inside = voiced[start:start + 10 * SR]
        if inside.sum() >= SR:
            pitch = f0[start:start + 10 * SR][inside]
            quotients.append(100 * float(np.std(pitch)) / float(np.mean(pitch)))
    truth = {"median_hz": float(np.median(f0[voiced])),
             "pitch_sd_semitones": float(np.std(12 * np.log2(f0[voiced] / np.median(f0[voiced])))),
             "pvq_pct": float(np.mean(quotients))}
    return signal, truth


def build(folder: str) -> dict:
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    words, total = _timeline()
    signal, truth = _synthesise(words, total)
    wavfile.write(out / "speech.wav", SR, np.round(np.clip(signal, -1, 1) * 32767).astype(np.int16))

    items = []
    for i, w in enumerate(words):
        if i:
            items.append({"text": " ", "start": words[i - 1]["end"], "end": w["start"],
                          "type": "spacing", "speaker_id": "speaker_0", "logprob": 0.0})
        items.append({"text": w["raw"], "start": w["start"], "end": w["end"], "type": "word",
                      "speaker_id": "speaker_0", "logprob": w["logprob"]})
    scribe = {"language_code": "en", "language_probability": 0.99,
              "text": " ".join(s for s, _, _ in SPEECH), "words": items}
    (out / "scribe.json").write_text(json.dumps(scribe))

    gemini = {"id": "v1_test", "object": "interaction", "status": "completed", "model": "gemini-3.8-flash",
              "steps": [{"type": "thought", "summary": []},
                        {"type": "model_output", "content": [{"type": "text", "text": json.dumps(GEMINI_ANNOTATIONS)}]}]}
    (out / "gemini.json").write_text(json.dumps(gemini))

    span = words[-1]["end"] - words[0]["start"]
    truth.update({"word_count": len(words), "wpm": 60 * len(words) / span, "span": span,
                  "uptalk_sentences": sum(r for _, r, _ in SPEECH)})
    return {"audio": str(out / "speech.wav"), "scribe": scribe, "gemini_response": gemini,
            "annotations": GEMINI_ANNOTATIONS, "truth": truth, "topic": TOPIC}
