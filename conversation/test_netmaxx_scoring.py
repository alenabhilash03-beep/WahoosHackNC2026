"""Synthetic end-to-end test for netmaxx_scoring.py.

Builds a fake user mic track (harmonic 'voice' per word, with controlled pitch
contours, pauses and timing), matching Scribe-style word timestamps, and persona
turns, then checks the engine flags what it should.
"""
import json
import os
import tempfile
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netmaxx_scoring import score_session, syllables  # noqa: E402

SR = 16000


def synth(words_plan, total_s, base_hz, flat=False):
    """words_plan: list of (text, start, dur, st_start, st_end)."""
    y = np.zeros(int(total_s * SR))
    rng = np.random.default_rng(0)
    y += rng.normal(0, 1e-4, y.size)
    for text, start, dur, st0, st1 in words_plan:
        n = int(dur * SR)
        i0 = int(start * SR)
        st = np.linspace(st0, st1, n) if not flat else np.zeros(n)
        f0 = base_hz * 2 ** (st / 12.0)
        phase = 2 * np.pi * np.cumsum(f0) / SR
        sig = sum((1.0 / k) * np.sin(k * phase) for k in range(1, 13))
        env = np.ones(n)
        ramp = int(0.015 * SR)
        env[:ramp] = np.linspace(0, 1, ramp)
        env[-ramp:] = np.linspace(1, 0, ramp)
        y[i0:i0 + n] += 0.25 * sig * env
    return y


def plan_sentence(text, t, contour, slow=1.0, gap=0.05):
    """contour: function(i, n) -> (st_start, st_end) for word i of n."""
    toks = text.split()
    out = []
    for i, w in enumerate(toks):
        dur = 0.16 * syllables(w) * slow
        s0, s1 = contour(i, len(toks))
        out.append((w, round(t, 3), round(dur, 3), s0, s1))
        t += dur + gap
    return out, t


def lively(i, n):
    # natural-ish declination with word-to-word movement, falling end
    base = 2.5 * np.sin(i * 1.3) - 1.5 * i / max(n - 1, 1)
    if i == n - 1:
        return base, base - 2.5
    return base, base + 0.8 * np.cos(i)


def lifted(i, n):
    # key sentence: accent peak on the middle words, falling end
    if n // 2 - 1 <= i <= n // 2 + 1:
        return 3.0, 5.0
    if i == n - 1:
        return 0.0, -3.0
    return 0.5, 1.0


def uptalk(i, n):
    base = 1.0 * np.sin(i)
    if i == n - 1:
        return 0.0, 6.0          # rises on a statement
    return base, base


def build(flat=False):
    plan = []
    t = 4.5
    s, t = plan_sentence("Oh nice, how was Lisbon?", t, lively)
    plan += s
    s, t = plan_sentence("Did you get to see the coast?", t + 0.35, lively)
    plan += s
    user_turn1_end = t

    t = 14.4
    s, t = plan_sentence("I am building a voice app that coaches people on conversations.", t, lively)
    plan += s
    s, t = plan_sentence("Our main goal is helping students land referrals.", t + 0.6, lifted, slow=1.35)
    plan += s
    s, t = plan_sentence("It um kind of works right now.", t + 0.4, uptalk)
    plan += s

    t = 28.0     # interruption during persona turn 3
    s, t = plan_sentence("Students mostly and new grads looking for jobs.", t, lively)
    plan += s

    audio = synth(plan, 36.0, base_hz=130.0, flat=flat)
    words = [{"text": w, "start": st, "end": round(st + d, 3), "type": "word"} for w, st, d, _, _ in plan]
    agent_turns = [
        {"text": "Hey, thanks for making time. I just got back from Lisbon last week so I'm a bit jet lagged.",
         "start": 0.0, "end": 4.0},
        {"text": "It was great, the coast near Cascais was beautiful. So what are you working on these days?",
         "start": user_turn1_end + 0.8, "end": 14.0},
        {"text": "That sounds useful. Who is your target user, and how do you",
         "start": 25.5, "end": 28.6, "interrupted": True},
        {"text": "Got it, students make sense.", "start": 31.5, "end": 33.0},
    ]
    session = {
        "user_words": words,
        "agent_turns": agent_turns,
        "hooks": [{"id": "lisbon_trip", "keywords": ["lisbon", "portugal"]},
                  {"id": "hiring_team", "keywords": ["hiring", "headcount"]}],
        "key_sentence": "My main goal is helping students land referrals",
    }
    return audio, session


if __name__ == "__main__":
    scratch = tempfile.mkdtemp()
    results = {}
    for name, flat in (("lively", False), ("monotone", True)):
        audio, session = build(flat)
        wav = f"{scratch}/{name}.wav"
        sf.write(wav, audio, SR)
        with open(f"{scratch}/{name}_session.json", "w") as f:
            json.dump(session, f, indent=2)
        results[name] = score_session(wav, session)

    lv, mono = results["lively"], results["monotone"]
    print(json.dumps({k: lv[k] for k in ("stats", "metrics", "subscores", "main_message")}, indent=2))
    print("\nEVENTS (lively):")
    for e in lv["events"]:
        print(f"  {e['t']:>6}  {'+' if e['good'] else '-'} {e['type']:<14} {e['detail']}")
    print("\nMONOTONE stats:", mono["stats"], " pitch_sd:", mono["metrics"]["pitch_sd_st"])

    m = lv["metrics"]
    checks = {
        "follow-up detected": m["follow_ups"] >= 1,
        "lisbon hook recalled": any(h["id"] == "lisbon_trip" and h["recalled"] for h in m["hooks"]),
        "unplanted hook excluded": any(h["id"] == "hiring_team" and not h["planted"] for h in m["hooks"]),
        "interruption caught": m["interruptions"] == 1,
        "filler counted": any(e["type"] == "filler" for e in lv["events"]),
        "hedge counted": m["hedge_per_100w"] > 0,
        "uptalk flagged": any(e["type"] == "rising_ending" for e in lv["events"]),
        "main message found": lv["main_message"] is not None,
        "key pause passes": lv["main_message"] and lv["main_message"]["checks"]["set_up_by_pause"],
        "key lift passes": lv["main_message"] and lv["main_message"]["checks"]["pitch_lifted"],
        "key slowdown passes": lv["main_message"] and lv["main_message"]["checks"]["slowed_down"],
        "key clean and landed": lv["main_message"] and lv["main_message"]["checks"]["clean_and_landed"],
        "main message 100 when all pass": lv["main_message"] and lv["main_message"]["score"] == 100.0,
        "monotone key sentence not lifted": mono["main_message"] and not mono["main_message"]["checks"]["pitch_lifted"],
        "monotone scores lower DELIVERY": (mono["stats"]["DELIVERY"] or 0) < (lv["stats"]["DELIVERY"] or 0),
        "deterministic rerun": score_session(f"{scratch}/lively.wav", build(False)[1]) == lv,
    }
    print("\nCHECKS:")
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    sys.exit(0 if all(checks.values()) else 1)
