"""Turn raw material into numbers. Everything in this file is plain, repeatable code.

Inputs:  ElevenLabs word timings, our own pitch/loudness track, Gemini's annotations.
Output:  one flat dict  metric name -> number (or None when it cannot be measured),
         plus the evidence behind those numbers for showing to the player.
"""
from __future__ import annotations

import math
import re

import numpy as np

from .extract import MANIPULATIVE_FALLACIES, REASONING_FALLACIES, APPEAL_TYPES, SUPPORT_TYPES

EL = "ElevenLabs timings + our code"
DSP = "our audio analysis"
TXT = "ElevenLabs transcript + our word lists"
GEM = "Gemini extraction + our verification"

# name: (label, unit, where it comes from)
CATALOG = {
    "words_per_minute": ("Speaking rate", "wpm", EL),
    "pace_variation_pct": ("Pace variation between windows", "%", EL),
    "pause_time_pct": ("Time spent in pauses", "%", EL),
    "time_used_pct": ("Allotted time used", "%", EL),
    "long_pause_count": ("Long pauses", "count", EL),
    "fillers_per_100_words": ("Filler words", "per 100 words", TXT),
    "vocabulary_variety_pct": ("Vocabulary variety", "%", TXT),
    "clarity_pct": ("Word clarity (recognition confidence)", "%", "ElevenLabs word confidence + our code"),
    "repeated_word_count": ("Stumbles (word said twice in a row)", "count", TXT),
    "pitch_variation_pct": ("Pitch variation (PVQ: pitch SD / mean, per 10 s)", "%", DSP),
    "pitch_variation_semitones": ("Pitch variation in semitones (reference only)", "semitones", DSP),
    "loudness_variation_db": ("Loudness variation (emphasis)", "dB", DSP),
    "uptalk_pct": ("Statements ending on a rising pitch", "%", "ElevenLabs timings + our audio analysis"),
    "hedges_per_100_words": ("Hedging phrases", "per 100 words", TXT),
    "monotone_window_count": ("Monotone stretches", "count", DSP),
    "supported_arguments": ("Arguments backed by reasoning or evidence", "count", GEM),
    "structure_points": ("Thesis and conclusion present", "of 2", GEM),
    "counterargument_level": ("Other side addressed", "0-2", GEM),
    "connectives_per_100_words": ("Reasoning connectives", "per 100 words", TXT),
    "reasoning_fallacy_count": ("Reasoning fallacies", "count", GEM),
    "unsupported_claim_count": ("Claims with no support", "count", GEM),
    "stance_mismatch": ("Argued the opposite side", "0/1", GEM),
    "stance_unclear": ("Side not clear", "0/1", GEM),
    "off_topic": ("Speech does not address the question", "0/1", GEM),
    "topic_drift": ("Speech drifts away from the question", "0/1", GEM),
    "emotional_appeals": ("Emotional appeals", "count", GEM),
    "appeal_variety": ("Kinds of emotional appeal", "count", GEM),
    "emotion_words_per_100_words": ("Emotionally charged words", "per 100 words", TXT),
    "audience_address_per_100_words": ("Speaking to the audience (you / we / our)", "per 100 words", TXT),
    "manipulative_appeal_count": ("Fear-mongering or personal attacks", "count", GEM),
}


# ----------------------------------------------------------------- text helpers
def normalise(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"[^a-z0-9']", "", text).strip("'")


def split_words(text: str) -> list[str]:
    return [w for w in (normalise(part) for part in text.split()) if w]


def find_phrases(tokens: list[str], phrases: list[str]) -> list[tuple[int, int]]:
    """Greedy, non-overlapping, longest-first phrase matches -> [(start index, length)]."""
    patterns = sorted({tuple(split_words(p)) for p in phrases if split_words(p)},
                      key=lambda p: (-len(p), p))
    hits, i = [], 0
    while i < len(tokens):
        for pattern in patterns:
            if tuple(tokens[i:i + len(pattern)]) == pattern:
                hits.append((i, len(pattern)))
                i += len(pattern)
                break
        else:
            i += 1
    return hits


def drop_phrases(tokens: list[str], phrases: list[str]) -> list[str]:
    skip = {j for start, length in find_phrases(tokens, phrases) for j in range(start, start + length)}
    return [t for j, t in enumerate(tokens) if j not in skip]


def words_from_scribe(scribe: dict) -> list[dict]:
    """Keep actual timed words; malformed timestamps cannot be used as evidence."""
    words = []
    for item in scribe.get("words") or []:
        if not isinstance(item, dict) or item.get("type", "word") != "word":
            continue
        raw = item.get("text")
        if not isinstance(raw, str) or not (token := normalise(raw)):
            continue
        if isinstance(item.get("start"), bool) or isinstance(item.get("end"), bool):
            continue
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            continue
        logprob = item.get("logprob")
        if (not isinstance(logprob, (int, float)) or isinstance(logprob, bool)
                or not math.isfinite(logprob) or logprob > 0):
            logprob = None
        words.append({"t": token, "raw": raw.strip(), "start": start, "end": end,
                      "logprob": logprob,
                      "confidence": math.exp(logprob) if logprob is not None else None})
    return words


def _per_100(count: int, total: int) -> float:
    return 100.0 * count / total if total else 0.0


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


# -------------------------------------------------------------- pace / diction
def pace_metrics(words: list[dict], session: dict, analysis: dict) -> tuple[dict, dict]:
    first, last = words[0]["start"], words[-1]["end"]
    span = max(last - first, 1e-6)
    gaps = [(a["end"], b["start"] - a["end"]) for a, b in zip(words, words[1:])]
    pauses = [(round(at, 2), round(gap, 2)) for at, gap in gaps if gap >= analysis["pause_min_seconds"]]
    long_pauses = [p for p in pauses if p[1] >= analysis["long_pause_seconds"]]

    window = analysis["pace_window_seconds"]
    rates, start = [], first
    while start < last:
        length = min(window, last - start)
        if length >= window / 2:      # ignore a short leftover at the end
            inside = sum(1 for w in words if start <= (w["start"] + w["end"]) / 2 < start + length)
            rates.append(60.0 * inside / length)
        start += window
    variation = None
    if len(rates) >= 3 and np.mean(rates) > 0:
        variation = 100.0 * float(np.std(rates)) / float(np.mean(rates))

    metrics = {
        "words_per_minute": 60.0 * len(words) / span,
        "pace_variation_pct": variation,
        "pause_time_pct": 100.0 * sum(gap for _, gap in pauses) / span,
        "time_used_pct": min(100.0, 100.0 * span / session["allotted_seconds"]),
        "long_pause_count": len(long_pauses),
    }
    evidence = {"speaking_seconds": round(span, 2), "window_wpm": [round(r, 1) for r in rates],
                "long_pauses": [{"at_seconds": at, "seconds": gap} for at, gap in long_pauses]}
    return metrics, evidence


def diction_metrics(words: list[dict], lexicons: dict, analysis: dict) -> tuple[dict, dict, list[str]]:
    tokens = [w["t"] for w in words]
    filler_hits = find_phrases(tokens, lexicons["fillers"])
    content = drop_phrases(tokens, lexicons["fillers"])

    size = analysis["vocabulary_window_words"]
    if len(content) >= size:
        ratios = [len(set(content[i:i + size])) / size for i in range(len(content) - size + 1)]
        variety = 100.0 * sum(ratios) / len(ratios)
    else:
        variety = 100.0 * len(set(content)) / len(content) if content else None

    confidences = [math.exp(w["logprob"]) for w in words if isinstance(w["logprob"], (int, float))]
    repeats = [content[i] for i in range(1, len(content)) if content[i] == content[i - 1]]

    metrics = {
        "fillers_per_100_words": _per_100(len(filler_hits), len(tokens)),
        "vocabulary_variety_pct": variety,
        "clarity_pct": 100.0 * sum(confidences) / len(confidences) if confidences else None,
        "repeated_word_count": len(repeats),
    }
    evidence = {"fillers": [" ".join(tokens[s:s + n]) for s, n in filler_hits], "repeated_words": repeats}
    return metrics, evidence, content


# ------------------------------------------------------------------------ tone
def _sentences(words: list[dict]) -> list[tuple[int, int, str]]:
    out, start = [], 0
    for i, word in enumerate(words):
        tail = word["raw"].rstrip("\"')”")
        if tail[-1:] in ".!?":
            out.append((start, i, tail[-1]))
            start = i + 1
    return out


def tone_metrics(words: list[dict], track, lexicons: dict, analysis: dict) -> tuple[dict, dict]:
    tokens = [w["t"] for w in words]
    hedge_hits = find_phrases(tokens, lexicons["hedges"])
    metrics = {"hedges_per_100_words": _per_100(len(hedge_hits), len(tokens)),
               "pitch_variation_pct": None, "pitch_variation_semitones": None,
               "loudness_variation_db": None, "uptalk_pct": None, "monotone_window_count": None}
    evidence = {"hedges": [" ".join(tokens[s:s + n]) for s, n in hedge_hits],
                "uptalk_sentences": [], "monotone_ranges": []}
    if track is None or int(track.voiced.sum()) < 100:       # under 1 s of voiced speech
        return metrics, evidence

    voiced = track.voiced
    metrics["pitch_variation_semitones"] = float(np.std(track.semitones[voiced]))
    metrics["loudness_variation_db"] = float(np.std(track.loudness_db[voiced]))
    evidence["median_pitch_hz"] = round(track.median_f0, 1)

    # Pitch variation quotient (Hincks 2005): for each 10 s window, the standard deviation
    # of pitch divided by its mean, in Hz. The speaker's score is the mean over windows.
    window, quotients, start = analysis["pace_window_seconds"], [], 0.0
    while start < track.duration:
        inside = voiced & (track.times >= start) & (track.times < start + window)
        if inside.sum() >= 100:                               # at least 1 s of voiced speech
            pitch = track.f0[inside]
            quotient = 100.0 * float(np.std(pitch)) / float(np.mean(pitch))
            quotients.append(quotient)
            if quotient < analysis["monotone_pvq_pct"]:
                evidence["monotone_ranges"].append(
                    {"start": start, "end": min(start + window, track.duration)})
        start += window
    if quotients:
        metrics["pitch_variation_pct"] = sum(quotients) / len(quotients)
        metrics["monotone_window_count"] = sum(q < analysis["monotone_pvq_pct"] for q in quotients)
        evidence["window_pitch_variation_pct"] = [round(q, 1) for q in quotients]

    # Uptalk: a statement (ends in . or !) whose pitch rises over its last 0.8 s.
    eligible = 0
    for first, last, mark in _sentences(words):
        if mark == "?" or last - first + 1 < 4:
            continue
        end = words[last]["end"]
        begin = max(words[first]["start"], end - 0.8)
        tail = track.semitones[voiced & (track.times >= begin) & (track.times <= end)]
        if len(tail) < 10:
            continue
        eligible += 1
        rise = float(np.median(tail[-max(3, len(tail) * 3 // 10):]) - np.median(tail[:len(tail) // 2]))
        if rise >= analysis["uptalk_rise_semitones"]:
            evidence["uptalk_sentences"].append(
                {"ends_at_seconds": round(end, 2), "rise_semitones": round(rise, 1),
                 "text": " ".join(w["raw"] for w in words[max(first, last - 5):last + 1])})
    if eligible >= 3:
        metrics["uptalk_pct"] = 100.0 * len(evidence["uptalk_sentences"]) / eligible
    return metrics, evidence


# -------------------------------------------------------------- logic / emotion
def verify_extraction(extraction: dict, words: list[dict], lexicons: dict, side: str) -> tuple[dict, dict]:
    """Keep only what Gemini can prove with a real quote, then count it ourselves."""
    fillers = lexicons["fillers"]
    haystack = " " + " ".join(drop_phrases([w["t"] for w in words], fillers)) + " "
    dropped: list[dict] = []

    def quote_tokens(quote) -> list[str]:
        return drop_phrases(split_words(quote if isinstance(quote, str) else ""), fillers)

    def found(quote) -> bool:
        tokens = quote_tokens(quote)
        return len(tokens) >= 3 and f" {' '.join(tokens)} " in haystack

    def reject(kind: str, quote, reason: str) -> None:
        dropped.append({"kind": kind, "quote": quote if isinstance(quote, str) else "", "reason": reason})

    def present(kind: str, quote) -> bool:
        if not quote:
            return False
        if found(quote):
            return True
        reject(kind, quote, "quote not found in transcript")
        return False

    thesis = present("thesis", extraction.get("thesis_quote"))
    conclusion = present("conclusion", extraction.get("conclusion_quote"))
    counter = present("counterargument", extraction.get("counterargument_quote"))
    rebuttal = counter and present("rebuttal", extraction.get("rebuttal_quote"))

    arguments, kept_claims = [], []
    for arg in extraction.get("arguments") or []:
        if not isinstance(arg, dict):
            continue
        claim = arg.get("claim_quote", "")
        if not found(claim):
            reject("argument", claim, "claim quote not found in transcript")
            continue
        claim_tokens = quote_tokens(claim)
        if any(_jaccard(claim_tokens, other) > 0.6 for other in kept_claims):
            reject("argument", claim, "duplicate of an earlier claim")
            continue
        kept_claims.append(claim_tokens)
        support, kind = arg.get("support_quote", ""), arg.get("support_type")
        if arg.get("supports_stance") is not True:
            status = "does not support the speaker's side"
        elif kind not in SUPPORT_TYPES or kind == "none":
            status = "asserted without support"
        elif not found(support):
            status = "support quote not found in transcript"
        elif _jaccard(claim_tokens, quote_tokens(support)) >= 0.8:
            status = "support only repeats the claim"
        else:
            status = "supported"
        arguments.append({"claim": claim, "support_type": kind if status == "supported" else "none",
                          "support": support if status == "supported" else "", "status": status})

    def tagged(key: str, allowed: list[str], kind: str) -> list[dict]:
        kept, seen = [], []
        for item in extraction.get(key) or []:
            if not isinstance(item, dict) or item.get("type") not in allowed:
                continue
            quote = item.get("quote", "")
            if not found(quote):
                reject(kind, quote, "quote not found in transcript")
                continue
            tokens = quote_tokens(quote)
            if any(_jaccard(tokens, other) > 0.6 for other in seen):
                continue
            seen.append(tokens)
            kept.append({"type": item["type"], "quote": quote})
        return kept

    fallacies = tagged("fallacies", REASONING_FALLACIES + MANIPULATIVE_FALLACIES, "fallacy")
    appeals = tagged("emotional_appeals", APPEAL_TYPES, "emotional appeal")
    stance = extraction.get("stance") if extraction.get("stance") in ("yes", "no") else "unclear"
    # A missing or malformed answer gets the benefit of the doubt.
    on_topic = extraction.get("on_topic") if extraction.get("on_topic") in ("yes", "partly", "no") else "yes"

    metrics = {
        "supported_arguments": sum(a["status"] == "supported" for a in arguments),
        "unsupported_claim_count": sum(a["status"] != "supported" for a in arguments),
        "structure_points": int(thesis) + int(conclusion),
        "counterargument_level": int(counter) + int(bool(rebuttal)),
        "reasoning_fallacy_count": sum(f["type"] in REASONING_FALLACIES for f in fallacies),
        "manipulative_appeal_count": sum(f["type"] in MANIPULATIVE_FALLACIES for f in fallacies),
        "stance_mismatch": int(stance != "unclear" and stance != side),
        "stance_unclear": int(stance == "unclear"),
        "off_topic": int(on_topic == "no"),
        "topic_drift": int(on_topic == "partly"),
        "emotional_appeals": len(appeals),
        "appeal_variety": len({a["type"] for a in appeals}),
    }
    position = extraction.get("position") if isinstance(extraction.get("position"), str) else ""
    evidence = {"on_topic": on_topic, "position_read": position, "stance_argued": stance, "side_chosen": side, "thesis_found": thesis,
                "conclusion_found": conclusion, "counterargument_found": counter,
                "rebuttal_found": bool(rebuttal), "arguments": arguments, "fallacies": fallacies,
                "emotional_appeals": appeals, "discarded_from_gemini": dropped}
    return metrics, evidence


# ------------------------------------------------------------------------ all
def compute_features(scribe: dict, track, extraction: dict, lexicons: dict,
                     standards: dict, side: str) -> tuple[dict, dict]:
    session, analysis = standards["session"], standards["analysis"]
    words = words_from_scribe(scribe)
    evidence: dict = {"word_count": len(words)}
    if len(words) < 2:
        return {}, evidence

    pace, evidence["pace"] = pace_metrics(words, session, analysis)
    diction, evidence["diction"], content = diction_metrics(words, lexicons, analysis)
    tone, evidence["tone"] = tone_metrics(words, track, lexicons, analysis)
    argument, evidence["argument"] = verify_extraction(extraction, words, lexicons, side)

    # Counted on the transcript with fillers removed, so "you know" is not also an audience "you".
    counts = {name: find_phrases(content, lexicons[name])
              for name in ("connectives", "emotion_words", "audience_words")}
    text = {
        "connectives_per_100_words": _per_100(len(counts["connectives"]), len(words)),
        "emotion_words_per_100_words": _per_100(len(counts["emotion_words"]), len(words)),
        "audience_address_per_100_words": _per_100(len(counts["audience_words"]), len(words)),
    }
    evidence["word_lists"] = {name: [" ".join(content[s:s + n]) for s, n in hits]
                              for name, hits in counts.items()}

    features = {**pace, **diction, **tone, **argument, **text}
    # Round once, here, so the number shown to the player is the number that was bucketed.
    return {k: (None if v is None else round(float(v), 2)) for k, v in features.items()}, evidence
