# Spoken-argument grader

Grades a one-minute spoken argument on pace, diction, tone, logic and appeal to
emotion. ElevenLabs and Gemini supply raw material; **our code measures, checks
and scores it against a rubric you edit in `config/standards.yaml`.**

## Run it

```bash
pip install -r requirements.txt      # numpy, scipy, pyyaml, requests
# ffmpeg must be on the PATH (decodes browser recordings: webm, m4a, mp3 ...)

python demo.py                       # no API keys needed: synthetic recording + canned API responses
python tests/test_pipeline.py        # 20 checks, no network

cp .env.example .env                 # then add ELEVENLABS_API_KEY and GEMINI_API_KEY
python grade.py speech.webm --topic "Should the frontier of AI be paced?" --side yes --out report.json
```

From your backend it is one call:

```python
from grader import grade
report = grade("uploads/abc.webm", topic="Should the frontier of AI be paced?", side="yes")
report["overall"]            # {"score": 84.6, "letter": "B"}
report["factors"]["logic"]   # sub-grade, every metric, its bucket, every deduction
report["evidence"]           # quotes, pauses, fillers ... for the feedback screen
```

`sample_report.json` (written by `demo.py`) shows the full shape to build a UI against.

## The pipeline

| # | Step | Who does it | Output |
|---|------|-------------|--------|
| 1 | Transcribe | **ElevenLabs Scribe** (`grader/transcribe.py`) | every word with start time, end time, confidence; fillers kept |
| 2 | Listen | **our code** (`grader/audio.py`) | pitch and loudness track from the raw audio |
| 3 | Annotate | **Gemini** (`grader/extract.py`) | thesis, claims, support, counterargument, fallacies, emotional appeals, each as a verbatim quote |
| 4 | Measure | **our code** (`grader/features.py`) | discards any Gemini item whose quote is not in the transcript, then counts and measures 27 numbers |
| 5 | Score | **our code** (`grader/scoring.py`) | buckets, deductions and weights from `standards.yaml` -> five sub-grades and a main grade |

Neither model ever produces a score. Gemini cannot raise a grade by inventing
material: unverifiable quotes are dropped and listed in
`report["evidence"]["argument"]["discarded_from_gemini"]`.

## Why the grade is repeatable

- Steps 2, 4 and 5 are plain code: same inputs, same numbers.
- Steps 1 and 3 run once per recording and are saved in `cache/` (keyed by the
  audio's SHA-256, and by transcript + topic + model + prompt version). Grading
  the same recording again never calls an API and returns the identical report.
- Change the rubric and old recordings re-grade from the cache for free. Every
  report carries `standards_id`, a hash of the rubric that produced it.
- Both APIs are also sent a fixed seed. Their docs call seeded output best-effort,
  so the cache, not the seed, is the guarantee. Two *different* recordings of the
  same words are separate inputs and may differ slightly.

## Editing the standards

Everything is in `config/standards.yaml`; the header explains the format.

```yaml
words_per_minute:
  weight: 50
  buckets:
    - {from: 140, to: 170, score: 100}   # from is inclusive, to is exclusive
    - {from: 130, to: 140, score: 85}
  else: 25                               # anything that matches no bucket
deductions:
  - {metric: long_pause_count, free: 1, points_each: 5, max_points: 20}
```

- Factor score = weighted average of its metric scores, minus deductions, clamped to 0-100.
- Overall = weighted average of factor scores. Weights are relative, so deleting a
  factor or metric re-balances the rest.
- `python grade.py --list-metrics` lists every metric you can bucket or deduct on.
- Word lists (fillers, hedges, connectives, emotion words) are in `config/lexicons.yaml`.
- Overlapping buckets, unknown metric names and bad numbers are rejected at load time.
- To add a new metric: compute it in `features.py`, add it to `CATALOG`, use it in the YAML.

## Known limits

- The default bucket numbers are reasonable starting points, not calibrated values.
  Record a few strong and weak speeches, look at `report["measurements"]`, and tune.
- The live ElevenLabs and Gemini calls follow the current API docs and are tested
  against canned responses in the documented shape. They have not been run against
  the real services from this build environment.
- The pitch tracker is verified against a synthetic signal with a known contour and
  gives plausible values on synthesised speech. It has not been checked on real
  microphone recordings; background noise will affect it.
- `clarity_pct` is the transcriber's confidence, so a strong accent or a poor
  microphone lowers it. Reduce its weight or remove it if that is unfair for your players.
- Tone measures how the voice sounds (inflection, emphasis, uptalk, hedging), not
  how the speaker feels.
- Logic records whether claims were backed up, not whether they are true.
- `cache/` holds transcripts of player speech. Treat it as user data.
