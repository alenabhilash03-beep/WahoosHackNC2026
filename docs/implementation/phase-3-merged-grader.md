# Phase 3 - Merged grader

**Goal:** one grader package scores both debate speeches and conversations, with the measurement
fixes from the plan's grader comparison, and every existing test still passes.

| | |
| --- | --- |
| Lane | C |
| Hours | 2 to 12 |
| Needs | Phase 0 |

Phase 4 does not wait for this: until the merge lands, the post-call worker calls
`conversation/netmaxx_scoring.py` directly (the "fast path"). Ship the fast path first, then merge.

## Done when

- [ ] `python softskills-grader/tests/test_pipeline.py` passes all of its existing tests
- [ ] New `softskills-grader/tests/test_conversation.py` passes (the 16 checks from
      `conversation/test_netmaxx_scoring.py`, ported)
- [ ] New `softskills-grader/tests/test_fixes.py` passes:
  - the 2 rising statements in the fixture are still found when their "." becomes "?"
    (the current code finds 0 of 2)
  - with all punctuation stripped, `uptalk_pct` is measured instead of `None`
  - a `curve:` metric moves by less than one point when its value moves slightly
- [ ] `grade(..., mode="coffee_chat", context={...})` returns a report whose factors are PACE,
      DELIVERY, CONFIDENCE, LISTENING and FLOW
- [ ] `standards_id` differs between the debate and coffee-chat rubrics

## Steps

Work on a branch: `git checkout -b grader-merge`. Commit after every step with its tests green.

### 1. Fast path for Phase 4 (30 min)

Nothing to build: `server/app/grading.py` already imports `score_session`. Tell lane A and the
Phase 4 worker the output shape: `stats`, `metrics`, `subscores`, `main_message`, `events`
(see `conversation/netmaxx_scoring.py`, bottom of `score_session`).

### 2. Pitch tracking: keep the built-in tracker

No change needed. PR #2 made the built-in tracker drop the two frames at each edge of every voiced
stretch, and it now matches Praat on the test audio: 2.04 semitones on the fixture (true value
2.04) and 0.002 on a word-by-word flat voice, against 0.014 for Praat. That keeps the grader free of
a Praat dependency. Re-check on 5 real microphone recordings during Phase 9 calibration.

### 3. Uptalk decided from words, not punctuation

In `features.py`, `tone_metrics` skips sentences whose final mark is "?". Speech-to-text often
writes "?" on a rising statement, which hides exactly the uptalk being measured. Replace the
`_sentences` loop test:

```python
QUESTION_STARTS = {"who", "what", "when", "where", "why", "how", "which", "whose", "do", "does", "did",
                   "are", "is", "was", "were", "have", "has", "had", "can", "could", "would", "will",
                   "should", "shall", "may", "might"}
TAGS = (("right",), ("you", "know"), ("isn't", "it"), ("don't", "you", "think"))


def is_real_question(tokens: list[str]) -> bool:
    return bool(tokens) and (tokens[0] in QUESTION_STARTS or any(tuple(tokens[-len(t):]) == t for t in TAGS))
```

and in the loop: `if is_real_question([w["t"] for w in words[first:last + 1]]) or last - first + 1 < 4: continue`.

When the transcript has no sentence punctuation at all, `_sentences` returns nothing. Make it fall
back to splitting at silent gaps of 0.4 s or more:

```python
def _sentences(words):
    out, start = [], 0
    for i, word in enumerate(words):
        tail = word["raw"].rstrip("\"')”")
        if tail[-1:] in ".!?":
            out.append((start, i, tail[-1])); start = i + 1
    if out:
        return out
    for i in range(len(words) - 1):                       # no punctuation: split on pauses
        if words[i + 1]["start"] - words[i]["end"] >= 0.4:
            out.append((start, i, ".")); start = i + 1
    if start < len(words):
        out.append((start, len(words) - 1, "."))
    return out
```

### 4. Remove the accent-sensitive metric from scoring

The validator rejects a weight of 0, so delete `clarity_pct` from the `diction:` metrics in
`config/standards.yaml`. It is still measured and still appears in `report["measurements"]` for
display. Bump `version:`.

### 5. Continuous curves next to buckets

Buckets jump 15 to 25 points at their edges, so a small improvement can show as zero change in a
rematch. Allow a metric to use a curve instead:

```yaml
words_per_minute:
  weight: 3
  source: "Guyer et al. 2025"          # shown on the card
  curve: {band: [100, 140, 195, 240]}   # 0 below 100, 100 from 140 to 195, 0 above 240
```

`config.py`: in `validate`, accept a metric that has either non-empty `buckets` or a `curve` with
exactly one of `band: [a, b, c, d]` (a < b <= c < d), `lower_is_better: [full, zero]`,
`higher_is_better: [zero, full]`. `scoring.py`:

```python
def curve_score(value: float, curve: dict) -> tuple[float, str]:
    if "band" in curve:
        a, b, c, d = curve["band"]
        if value <= a or value >= d:
            s = 0.0
        elif value < b:
            s = 100 * (value - a) / (b - a)
        elif value <= c:
            s = 100.0
        else:
            s = 100 * (d - value) / (d - c)
        return round(s, 1), f"band {a}-{b}-{c}-{d}"
    if "lower_is_better" in curve:
        full, zero = curve["lower_is_better"]
        s = 100.0 if value <= full else 0.0 if value >= zero else 100 * (zero - value) / (zero - full)
        return round(s, 1), f"full at {full} or less, zero at {zero}"
    zero, full = curve["higher_is_better"]
    s = 100.0 if value >= full else 0.0 if value <= zero else 100 * (value - zero) / (full - zero)
    return round(s, 1), f"zero at {zero}, full at {full} or more"
```

and in `score_factor`: `score, label = curve_score(value, rule["curve"]) if "curve" in rule else bucket_score(value, rule)`.
Leave the debate rubric on buckets unless the team decides otherwise; use curves in the new
coffee-chat rubric.

### 6. Conversation features inside the grader

Create `softskills-grader/grader/conversation.py` by porting these pieces of
`conversation/netmaxx_scoring.py`, adapted to the grader's word dicts (`t`, `raw`, `start`, `end`):

| From `netmaxx_scoring.py` | Becomes metric (CATALOG name) | Unit |
| --- | --- | --- |
| `build_user_turns`, latency loop | `response_latency_s` (median) | s |
| interruption loop | `interruptions_per_10_min`, `backchannel_count` | count |
| talk share | `talk_share_pct` | % |
| follow-up loop | `follow_up_turn_pct` | % |
| hooks loop | `hooks_recalled_pct` | % |
| LSM block | `style_matching` | 0-1 |
| rate vs persona | `rate_match_ratio` | ratio |
| trailing-off loop | `trailing_off_pct` | % |
| main-message block | `main_message_checks` | 0-5 |

Signature:

```python
def conversation_metrics(words: list[dict], track, context: dict, lexicons: dict) -> tuple[dict, dict]:
    """context: {"agent_turns": [{"text","start","end"}], "hooks": [...], "key_sentence": str | None}.
    Returns (metrics, evidence) where evidence["events"] is the timestamped list for the replay."""
```

Register each new metric in `CATALOG` with `(label, unit, source)`. In `compute_features`, add a
`context=None` parameter and merge `conversation_metrics(...)` when `context` is given. In
`pipeline.grade`, add `mode: str = "debate"` and `context: dict | None = None`; pass `context` through.

### 7. One rubric per mode

`load_standards(config_dir)` reads `standards.yaml`. Change it to
`load_standards(config_dir, name="standards")` reading `f"{name}.yaml"`, and have
`grade(..., mode=...)` pick `"standards"` for debate and `"coffee_chat"` for conversations. Create
`config/coffee_chat.yaml` with the same `session`, `apis`, `analysis` and `grades` blocks and these
factors (weights are the scenario defaults; curves from the plan's "Measured metrics" table):

| Factor | Metrics |
| --- | --- |
| PACE | `words_per_minute` band 100/140/190/240, `rate_match_ratio` band 0.7/0.9/1.2/1.5, `pause_time_pct` |
| DELIVERY | `pitch_variation_pct`, `trailing_off_pct` lower 15/50, `main_message_checks` higher 0/5 |
| CONFIDENCE | `fillers_per_100_words` lower 2/8, `hedges_per_100_words` lower 1.5/6, `uptalk_pct` lower 15/50 |
| LISTENING | `follow_up_turn_pct` higher 0/35 (weight 4), `hooks_recalled_pct` higher 0/100 (3), `interruptions_per_10_min` lower 1/8 (2), `style_matching` higher 0.6/0.9 (1) |
| FLOW | `response_latency_s` lower 0.7/2.5, `talk_share_pct` band 20/50/70/85 |

`session.min_words_to_grade` can stay 40; set `session.allotted_seconds` to the scenario time limit.

### 8. Tests

- `tests/test_conversation.py`: port every check from `conversation/test_netmaxx_scoring.py`, calling
  `grade(..., mode="coffee_chat", transcript=..., extraction={}, context=...)` with the same
  synthetic audio.
- `tests/test_fixes.py`: the three checks in **Done when**. Build the "?" case from
  `tests/fixtures.py` by changing the raw text of the words `people.` and `together.` (the two
  rising sentences) to end in "?"; build the no-punctuation case by stripping `.,!?` from every word.

### 9. Switch Phase 4 over

When everything is green, merge `grader-merge` to main, and change the Phase 4 worker from
`score_session(...)` to `grade(..., mode="coffee_chat", context=...)`. The card format stays the
same: map factors to stats one to one.

## Pitfalls

- PR #2 removed loudness variation and pace variation from the debate rubric on purpose (no
  evidence). Do not re-add them while porting.
- Keep `pitch_variation_pct` (Hincks's quotient) as the debate rubric's pitch metric.
- Any change to a YAML file changes `standards_id`. Old reports keep their id, so cards can always
  say which rubric scored them.
