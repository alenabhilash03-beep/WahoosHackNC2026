"""
NetworkMaxx deterministic scoring engine.

Same audio + same transcript in -> same numbers out. No LLM calls in here.
Every metric is tied to a published finding (see the build plan doc); every
threshold that research does not pin down is listed in CALIBRATION so the team
can tune it on its own recordings.

Inputs
------
audio_path : mono or stereo WAV of the USER'S MIC ONLY (record with headphones,
             so the persona's voice is not in it). Convert webm first:
             ffmpeg -i user.webm -ac 1 -ar 16000 user.wav
session    : dict with
    user_words   [{"text": "Lisbon?", "start": 12.31, "end": 12.80}, ...]
                 word-level timestamps from ElevenLabs Scribe (type == "word"),
                 seconds from the start of the mic recording
    agent_turns  [{"text": "...", "start": 3.1, "end": 9.4, "interrupted": false}, ...]
                 persona speaking intervals logged by the browser on the same clock
    hooks        [{"id": "lisbon", "keywords": ["lisbon", "portugal"]}, ...]  (optional)
    key_sentence "the sentence the user meant as their main point"            (optional)

Usage
-----
    python netmaxx_scoring.py user.wav session.json > card.json
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field
from statistics import median

import numpy as np
import parselmouth

# --------------------------------------------------------------------------
# Calibration: starting values, tune on ~10 team recordings
# --------------------------------------------------------------------------
CALIBRATION = {
    "pitch_floor_hz": 75.0,
    "pitch_ceiling_hz": 500.0,
    "turn_split_gap_s": 1.2,          # silence that ends a user turn
    "pause_min_s": 0.25,              # a silent pause inside a turn
    "long_pause_s": 2.0,              # flagged on the timeline
    "wpm_band": (100, 140, 190, 240),          # zero, full, full, zero
    "rate_match_band": (0.7, 0.9, 1.2, 1.5),   # user wpm / persona wpm
    "pauses_per_min_band": (0.5, 3, 12, 25),
    "filler_per_100w": (2.0, 8.0),             # full credit at/below, zero at/above
    "hedge_per_100w": (1.5, 6.0),
    "pitch_sd_st_band": (1.0, 2.5, 5.5, 8.0),  # monotone ... overacting
    "rise_threshold_st": 2.0,                  # statement ending rise = question-like
    "rising_share": (0.15, 0.5),
    "trail_drop_db": 6.0,
    "trailing_share": (0.15, 0.5),
    "latency_s": (0.7, 2.5),
    "interrupt_min_s": 1.0,                    # shorter overlaps are backchannels
    "interrupt_per_10min": (1.0, 8.0),
    "talk_share_band": (0.2, 0.5, 0.7, 0.85),
    "followup_rate_full": 0.35,                # share of user turns with a follow-up
    "lsm": (0.6, 0.9),
    "key_pause_s": 0.3,
    "key_lift_st": 2.0,
    "key_range_ratio": 1.25,
    "key_slow_ratio": 0.9,
}

FILLERS = {"um", "uh", "er", "erm", "ah", "uhm", "umm", "uhh", "hmm", "mm"}
HEDGE_PHRASES = [
    "kind of", "sort of", "kinda", "sorta", "maybe", "i guess", "probably",
    "perhaps", "a little bit", "i suppose", "somewhat", "or something",
    "or whatever", "i'm not sure", "not sure",
]
TAG_QUESTIONS = ["right", "you know", "isn't it", "don't you think", "yeah"]
QUESTION_STARTS = {
    "who", "what", "when", "where", "why", "how", "which", "whose",
    "do", "does", "did", "are", "is", "was", "were", "have", "has", "had",
    "can", "could", "would", "will", "should", "shall", "may", "might",
}
REFERENTIAL = {"that", "it", "there", "they", "those", "this", "them"}
STOPWORDS = set("""
a about above after again against all am an and any are as at be because been
before being below between both but by can could did do does doing down during
each few for from further had has have having he her here hers herself him
himself his how i if in into is it its itself just me more most my myself no nor
not now of off on once only or other our ours ourselves out over own same she
should so some such than that the their theirs them themselves then there these
they this those through to too under until up very was we were what when where
which while who whom why will with would you your yours yourself yourselves
yeah oh okay ok like really actually also get got go going gonna know think
thing things lot well um uh so just kind sort mean right much many one
""".split())

# Approximate open stand-ins for the 9 LIWC function-word categories used in
# language style matching (Ireland et al. 2011). LIWC itself is licensed.
LSM_CATEGORIES = {
    "personal_pronouns": {"i", "me", "my", "mine", "myself", "we", "us", "our", "ours",
                          "you", "your", "yours", "he", "him", "his", "she", "her",
                          "hers", "they", "them", "their", "theirs"},
    "impersonal_pronouns": {"it", "its", "this", "that", "these", "those", "anything",
                            "something", "everything", "nothing", "someone", "anyone"},
    "articles": {"a", "an", "the"},
    "prepositions": {"in", "on", "at", "to", "for", "with", "from", "of", "about",
                     "into", "over", "under", "after", "before", "by", "through"},
    "auxiliary_verbs": {"am", "is", "are", "was", "were", "be", "been", "being", "have",
                        "has", "had", "do", "does", "did", "will", "would", "can",
                        "could", "should", "shall", "may", "might", "must"},
    "adverbs": {"very", "really", "so", "just", "also", "too", "quite", "pretty",
                "even", "still", "already", "always", "never", "often", "actually"},
    "conjunctions": {"and", "but", "or", "because", "so", "although", "though",
                     "while", "if", "unless", "since", "whereas"},
    "negations": {"no", "not", "never", "none", "nobody", "nothing", "don't",
                  "doesn't", "didn't", "can't", "won't", "isn't", "aren't", "wasn't"},
    "quantifiers": {"all", "some", "many", "much", "few", "lot", "lots", "every",
                    "each", "several", "most", "more", "less", "any"},
}


# --------------------------------------------------------------------------
# Scoring curves (pure functions, 0..100)
# --------------------------------------------------------------------------
def band(x: float, lo_zero: float, lo_full: float, hi_full: float, hi_zero: float) -> float:
    """Trapezoid: 0 outside [lo_zero, hi_zero], 100 inside [lo_full, hi_full]."""
    if x <= lo_zero or x >= hi_zero:
        return 0.0
    if x < lo_full:
        return 100.0 * (x - lo_zero) / (lo_full - lo_zero)
    if x <= hi_full:
        return 100.0
    return 100.0 * (hi_zero - x) / (hi_zero - hi_full)


def lower_is_better(x: float, full_at: float, zero_at: float) -> float:
    if x <= full_at:
        return 100.0
    if x >= zero_at:
        return 0.0
    return 100.0 * (zero_at - x) / (zero_at - full_at)


def higher_is_better(x: float, zero_at: float, full_at: float) -> float:
    if x >= full_at:
        return 100.0
    if x <= zero_at:
        return 0.0
    return 100.0 * (x - zero_at) / (full_at - zero_at)


def mean_of(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def weighted(pairs) -> float | None:
    pairs = [(v, w) for v, w in pairs if v is not None]
    total = sum(w for _, w in pairs)
    return round(sum(v * w for v, w in pairs) / total, 1) if total else None


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def norm(token: str) -> str:
    return re.sub(r"[^a-z']", "", token.lower())


def stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "ly", "s"):
        if len(token) > 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def content_stems(text: str) -> set[str]:
    tokens = [norm(t) for t in text.split()]
    return {stem(t) for t in tokens if len(t) >= 3 and t not in STOPWORDS}


def syllables(word: str) -> int:
    w = norm(word)
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def phrase_count(text: str, phrases) -> int:
    padded = " " + re.sub(r"[^a-z' ]", " ", text.lower()) + " "
    padded = re.sub(r"\s+", " ", padded)
    return sum(padded.count(f" {p} ") for p in phrases)


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------
@dataclass
class Word:
    text: str
    start: float
    end: float

    @property
    def token(self) -> str:
        return norm(self.text)


@dataclass
class Sentence:
    words: list[Word]
    turn_index: int

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def is_lexical_question(self) -> bool:
        first = self.words[0].token
        clean = re.sub(r"[^a-z' ]", " ", self.text.lower()).strip()
        tagged = any(clean.endswith(" " + t) for t in TAG_QUESTIONS)
        return first in QUESTION_STARTS or tagged

    @property
    def is_question(self) -> bool:
        return self.text.rstrip().endswith("?") or self.is_lexical_question


@dataclass
class Turn:
    words: list[Word] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------
def build_user_turns(words: list[Word], agent_turns: list[dict]) -> list[Turn]:
    gap_split = CALIBRATION["turn_split_gap_s"]
    agent_starts = sorted(a["start"] for a in agent_turns)
    turns: list[Turn] = []
    for w in words:
        if turns:
            prev = turns[-1].words[-1]
            agent_between = any(prev.end <= s <= w.start for s in agent_starts)
            if w.start - prev.end > gap_split or agent_between:
                turns.append(Turn())
        else:
            turns.append(Turn())
        turns[-1].words.append(w)
    return turns


def build_sentences(turns: list[Turn]) -> list[Sentence]:
    sentences: list[Sentence] = []
    for ti, turn in enumerate(turns):
        current: list[Word] = []
        for w in turn.words:
            current.append(w)
            if re.search(r"[.?!]$", w.text.strip()):
                sentences.append(Sentence(current, ti))
                current = []
        if current:
            sentences.append(Sentence(current, ti))
    return sentences


# --------------------------------------------------------------------------
# Acoustics
# --------------------------------------------------------------------------
class Acoustics:
    def __init__(self, audio_path: str, speech_spans: list[tuple[float, float]]):
        snd = parselmouth.Sound(audio_path)
        if snd.n_channels > 1:
            snd = snd.convert_to_mono()
        pitch = snd.to_pitch_ac(
            time_step=0.01,
            pitch_floor=CALIBRATION["pitch_floor_hz"],
            pitch_ceiling=CALIBRATION["pitch_ceiling_hz"],
        )
        self.f0_t = np.asarray(pitch.xs())
        self.f0 = np.asarray(pitch.selected_array["frequency"])
        intensity = snd.to_intensity(minimum_pitch=CALIBRATION["pitch_floor_hz"], time_step=0.01)
        self.db_t = np.asarray(intensity.xs())
        self.db = np.asarray(intensity.values[0])

        in_speech = np.zeros_like(self.f0_t, dtype=bool)
        for s, e in speech_spans:
            in_speech |= (self.f0_t >= s - 0.05) & (self.f0_t <= e + 0.05)
        voiced = in_speech & (self.f0 > 0)
        self.median_f0 = float(np.median(self.f0[voiced])) if voiced.any() else None

    def semitones(self, start: float, end: float) -> np.ndarray:
        if not self.median_f0:
            return np.array([])
        m = (self.f0_t >= start) & (self.f0_t <= end) & (self.f0 > 0)
        st = 12.0 * np.log2(self.f0[m] / self.median_f0)
        return st[np.abs(st) <= 12.0]   # drop octave-jump errors

    def intensity(self, start: float, end: float) -> np.ndarray:
        m = (self.db_t >= start) & (self.db_t <= end)
        vals = self.db[m]
        return vals[np.isfinite(vals)]


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------
def score_session(audio_path: str, session: dict) -> dict:
    C = CALIBRATION
    words = [Word(w["text"], float(w["start"]), float(w["end"]))
             for w in session["user_words"] if w.get("type", "word") == "word"]
    words.sort(key=lambda w: w.start)
    agent_turns = sorted(session.get("agent_turns", []), key=lambda a: a["start"])
    hooks = session.get("hooks", [])
    events: list[dict] = []

    turns = build_user_turns(words, agent_turns)
    sentences = build_sentences(turns)
    acoustics = Acoustics(audio_path, [(t.start, t.end) for t in turns])

    user_time = sum(t.duration for t in turns)
    user_minutes = max(user_time / 60.0, 1e-6)
    n_words = len(words)
    all_text = " ".join(w.text for w in words)

    # ---- Pace ------------------------------------------------------------
    wpm = n_words / user_minutes
    agent_words = sum(len(a["text"].split()) for a in agent_turns)
    agent_time = sum(max(0.0, a["end"] - a["start"]) for a in agent_turns)
    agent_wpm = agent_words / (agent_time / 60.0) if agent_time > 0 else None
    rate_ratio = wpm / agent_wpm if agent_wpm else None
    pace_score = band(wpm, *C["wpm_band"])
    if rate_ratio is not None:
        pace_score = 0.75 * pace_score + 0.25 * band(rate_ratio, *C["rate_match_band"])

    # ---- Pauses ----------------------------------------------------------
    pauses, long_pauses = 0, 0
    for t in turns:
        for a, b in zip(t.words, t.words[1:]):
            gap = b.start - a.end
            if gap >= C["pause_min_s"]:
                pauses += 1
            if gap >= C["long_pause_s"]:
                long_pauses += 1
                events.append({"t": round(a.end, 2), "type": "long_pause",
                               "detail": f"{gap:.1f}s silence mid-answer", "good": False})
    pauses_per_min = pauses / user_minutes
    pause_score = band(pauses_per_min, *C["pauses_per_min_band"])

    # ---- Fillers and hedges ------------------------------------------------
    filler_words = [w for w in words if w.token in FILLERS]
    for w in filler_words:
        events.append({"t": round(w.start, 2), "type": "filler", "detail": w.token, "good": False})
    filler_rate = 100.0 * len(filler_words) / max(n_words, 1)
    hedge_count = phrase_count(all_text, HEDGE_PHRASES)
    hedge_rate = 100.0 * hedge_count / max(n_words, 1)
    filler_score = lower_is_better(filler_rate, *C["filler_per_100w"])
    hedge_score = lower_is_better(hedge_rate, *C["hedge_per_100w"])

    # ---- Pitch variation ---------------------------------------------------
    voiced_st = np.concatenate([acoustics.semitones(t.start, t.end) for t in turns]) \
        if turns else np.array([])
    pitch_sd = float(np.std(voiced_st)) if voiced_st.size > 20 else None
    pitch_score = band(pitch_sd, *C["pitch_sd_st_band"]) if pitch_sd is not None else None

    # ---- Sentence-level prosody: endings, trailing off, ranges, rates -------
    statements, rising, long_sents, trailing = 0, 0, 0, 0
    sent_ranges, sent_rates = [], []
    sent_info = []
    for s in sentences:
        info = {"rise_st": None, "range_st": None, "rate_sps": None}
        st = acoustics.semitones(s.start, s.end)
        if st.size >= 10:
            info["range_st"] = float(np.percentile(st, 90) - np.percentile(st, 10))
            sent_ranges.append(info["range_st"])
        dur = s.end - s.start
        if dur > 0.3:
            info["rate_sps"] = sum(syllables(w.text) for w in s.words) / dur
            sent_rates.append(info["rate_sps"])
        tail = acoustics.semitones(max(s.start, s.end - 0.5), s.end)
        if tail.size >= 6:
            k = max(2, int(round(tail.size * 0.4)))
            info["rise_st"] = float(np.median(tail[-k:]) - np.median(tail[:-k]))
            if not s.is_lexical_question:          # uptalk check on statements only
                statements += 1
                if info["rise_st"] > C["rise_threshold_st"]:
                    rising += 1
                    events.append({"t": round(s.end, 2), "type": "rising_ending",
                                   "detail": f"statement ended +{info['rise_st']:.1f} st, sounds unsure",
                                   "good": False})
        if dur >= 1.0:
            db = acoustics.intensity(s.start, s.end)
            if db.size >= 8:
                cut = int(db.size * 0.75)
                drop = float(np.mean(db[:cut]) - np.mean(db[cut:]))
                long_sents += 1
                if drop > C["trail_drop_db"]:
                    trailing += 1
                    events.append({"t": round(s.end, 2), "type": "trailing_off",
                                   "detail": f"volume fell {drop:.0f} dB at the end", "good": False})
        sent_info.append(info)

    rising_share = rising / statements if statements else None
    ending_score = lower_is_better(rising_share, *C["rising_share"]) if rising_share is not None else None
    trailing_share = trailing / long_sents if long_sents else None
    trailing_score = lower_is_better(trailing_share, *C["trailing_share"]) if trailing_share is not None else None

    # ---- Turn-taking: latency, interruptions, backchannels, talk share -------
    latencies, interruptions, backchannels = [], 0, 0
    for t in turns:
        overlapping = [a for a in agent_turns if a["start"] + 0.3 < t.start < a["end"]]
        if overlapping:
            if t.duration >= C["interrupt_min_s"] and len(t.words) >= 3:
                interruptions += 1
                events.append({"t": round(t.start, 2), "type": "interruption",
                               "detail": f"cut in: \"{' '.join(w.text for w in t.words[:6])}...\"",
                               "good": False})
            else:
                backchannels += 1
            continue
        prior = [a for a in agent_turns if a["end"] <= t.start]
        if prior:
            last_agent = prior[-1]
            earlier_user = [u for u in turns if last_agent["end"] <= u.start < t.start]
            if not earlier_user:
                latencies.append(t.start - last_agent["end"])
    median_latency = float(median(latencies)) if latencies else None
    latency_score = lower_is_better(median_latency, *C["latency_s"]) if median_latency is not None else None
    total_minutes = max((max([t.end for t in turns] + [a["end"] for a in agent_turns]) -
                         min([t.start for t in turns] + [a["start"] for a in agent_turns])) / 60.0, 1e-6) \
        if turns and agent_turns else user_minutes
    interrupt_rate = interruptions / total_minutes * 10.0
    interrupt_score = lower_is_better(interrupt_rate, *C["interrupt_per_10min"])
    talk_share = user_time / (user_time + agent_time) if (user_time + agent_time) > 0 else None
    talk_score = band(talk_share, *C["talk_share_band"]) if talk_share is not None else None

    # ---- Questions and follow-ups (Huang et al. 2017) -------------------------
    questions, follow_ups = 0, 0
    turns_with_followup = set()
    for s in sentences:
        if not s.is_question:
            continue
        questions += 1
        prior = [a for a in agent_turns if a["end"] <= s.start + 0.3]
        if not prior:
            continue
        last_agent = prior[-1]
        overlap = content_stems(s.text) & content_stems(last_agent["text"])
        first_in_reply = s.turn_index == next(
            (i for i, t in enumerate(turns) if t.start >= last_agent["end"] - 0.3), -1)
        referential = first_in_reply and any(w.token in REFERENTIAL for w in s.words)
        if overlap or referential:
            follow_ups += 1
            turns_with_followup.add(s.turn_index)
            why = f"picked up on '{sorted(overlap)[0]}'" if overlap else "followed up on their last point"
            events.append({"t": round(s.start, 2), "type": "follow_up",
                           "detail": why, "good": True})
    followup_rate = len(turns_with_followup) / max(len(turns), 1)
    followup_score = higher_is_better(followup_rate, 0.0, C["followup_rate_full"])

    # ---- Planted hooks (retention) ----------------------------------------
    hook_results = []
    for h in hooks:
        kw = {stem(norm(k)) for k in h["keywords"]}
        planted = next((a for a in agent_turns if content_stems(a["text"]) & kw), None)
        recalled_at = None
        if planted:
            for s in sentences:
                if s.start >= planted["end"] and content_stems(s.text) & kw:
                    recalled_at = s.start
                    events.append({"t": round(s.start, 2), "type": "hook_recalled",
                                   "detail": f"remembered: {h['id']}", "good": True})
                    break
        hook_results.append({"id": h["id"], "planted": planted is not None,
                             "recalled": recalled_at is not None})
    planted_hooks = [r for r in hook_results if r["planted"]]
    hook_score = (100.0 * sum(r["recalled"] for r in planted_hooks) / len(planted_hooks)
                  if planted_hooks else None)

    # ---- Language style matching (Ireland et al. 2011) ------------------------
    def category_rates(text: str) -> dict:
        toks = [norm(t) for t in text.split() if norm(t)]
        n = max(len(toks), 1)
        return {c: 100.0 * sum(t in ws for t in toks) / n for c, ws in LSM_CATEGORIES.items()}

    agent_text = " ".join(a["text"] for a in agent_turns)
    lsm = None
    if agent_text and all_text:
        u, a = category_rates(all_text), category_rates(agent_text)
        lsm = float(np.mean([1 - abs(u[c] - a[c]) / (u[c] + a[c] + 1e-4) for c in LSM_CATEGORIES]))
    lsm_score = higher_is_better(lsm, *C["lsm"]) if lsm is not None else None

    # ---- Main message delivery ------------------------------------------------
    main_message = None
    key_text = session.get("key_sentence")
    if key_text and sentences:
        key_tokens = {norm(t) for t in key_text.split() if norm(t)}

        def jaccard(s: Sentence) -> float:
            toks = {w.token for w in s.words if w.token}
            return len(toks & key_tokens) / max(len(toks | key_tokens), 1)

        best_i = max(range(len(sentences)), key=lambda i: jaccard(sentences[i]))
        if jaccard(sentences[best_i]) >= 0.3:
            ks, ki = sentences[best_i], sent_info[best_i]
            turn = turns[ks.turn_index]
            idx = turn.words.index(ks.words[0])
            if idx > 0:
                pause_before = ks.start - turn.words[idx - 1].end
            else:
                prior = [a for a in agent_turns if a["end"] <= ks.start]
                pause_before = ks.start - prior[-1]["end"] if prior else 0.0
            st = acoustics.semitones(ks.start, ks.end)
            peak = float(np.max(st)) if st.size else None
            typical_range = median(sent_ranges) if sent_ranges else None
            typical_rate = median(sent_rates) if sent_rates else None
            checks = {
                "set_up_by_pause": pause_before >= C["key_pause_s"],
                "pitch_lifted": bool(
                    (peak is not None and peak >= C["key_lift_st"]) or
                    (ki["range_st"] and typical_range and
                     ki["range_st"] >= C["key_range_ratio"] * typical_range)),
                "slowed_down": bool(ki["rate_sps"] and typical_rate and
                                    ki["rate_sps"] <= C["key_slow_ratio"] * typical_rate),
                "clean_and_landed": (
                    not any(w.token in FILLERS for w in ks.words)
                    and phrase_count(ks.text, HEDGE_PHRASES) == 0
                    and (ki["rise_st"] is None or ki["rise_st"] <= C["rise_threshold_st"])),
            }
            main_message = {
                "sentence": ks.text, "t": round(ks.start, 2),
                "checks": {"found": True, **checks},
                "score": 20.0 + 20.0 * sum(checks.values()),   # 5 checks x 20 points
                "measures": {
                    "pause_before_s": round(pause_before, 2),
                    "peak_st_above_median": round(peak, 2) if peak is not None else None,
                    "range_st": round(ki["range_st"], 2) if ki["range_st"] else None,
                    "typical_range_st": round(typical_range, 2) if typical_range else None,
                    "rate_sps": round(ki["rate_sps"], 2) if ki["rate_sps"] else None,
                    "typical_rate_sps": round(typical_rate, 2) if typical_rate else None,
                },
            }
            events.append({"t": round(ks.start, 2), "type": "main_message",
                           "detail": f"main point delivered, {1 + sum(checks.values())}/5 checks",
                           "good": sum(checks.values()) >= 3})

    # ---- Stats -----------------------------------------------------------------
    stats = {
        "PACE": mean_of([pace_score, pause_score]),
        "DELIVERY": mean_of([pitch_score, trailing_score,
                             main_message["score"] if main_message else None]),
        "CONFIDENCE": mean_of([filler_score, hedge_score, ending_score]),
        "LISTENING": weighted([(followup_score, 0.4), (hook_score, 0.3),
                               (interrupt_score, 0.2), (lsm_score, 0.1)]),
        "FLOW": mean_of([latency_score, talk_score]),
    }

    def r(x, d=2):
        return round(x, d) if isinstance(x, (int, float)) and x is not None else x

    return {
        "stats": stats,
        "metrics": {
            "wpm": r(wpm, 1), "persona_wpm": r(agent_wpm, 1), "rate_ratio": r(rate_ratio),
            "pauses_per_min": r(pauses_per_min, 1), "long_pauses": long_pauses,
            "filler_per_100w": r(filler_rate), "hedge_per_100w": r(hedge_rate),
            "pitch_sd_st": r(pitch_sd), "median_f0_hz": r(acoustics.median_f0, 1),
            "rising_statement_share": r(rising_share), "trailing_share": r(trailing_share),
            "median_latency_s": r(median_latency), "interruptions": interruptions,
            "backchannels": backchannels, "talk_share": r(talk_share),
            "questions": questions, "follow_ups": follow_ups, "followup_turn_rate": r(followup_rate),
            "hooks": hook_results, "lsm": r(lsm, 3),
            "user_words": n_words, "user_turns": len(turns),
        },
        "subscores": {k: r(v, 1) for k, v in {
            "pace": pace_score, "pauses": pause_score, "fillers": filler_score,
            "hedges": hedge_score, "pitch_variation": pitch_score, "endings": ending_score,
            "trailing": trailing_score, "latency": latency_score, "interruptions": interrupt_score,
            "talk_share": talk_score, "follow_ups": followup_score, "hooks": hook_score,
            "lsm": lsm_score}.items()},
        "main_message": main_message,
        "events": sorted(events, key=lambda e: e["t"]),
        "calibration": CALIBRATION,
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python netmaxx_scoring.py user.wav session.json")
    with open(sys.argv[2]) as f:
        session_data = json.load(f)
    print(json.dumps(score_session(sys.argv[1], session_data), indent=2, default=str))
