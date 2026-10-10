# Handoff: Debate Arena

State of the repo on Saturday, October 10, 2026. Read this first, then the
[Build Plan](Build%20Plan.md).

## What we are building

A speech and rhetoric game for two players on one laptop. Player 1 argues one side of a motion for
60 seconds; Player 2 goes after, prepares for 30 seconds with Player 1's claims on screen, and
argues the other side for 60 seconds, answering Player 1; Player 1 gets an optional 30-second reply.
Every speech is rated by `softskills-grader`, and `Arena/match_judge.py` picks the winner. A
rematch swaps who goes first.

**Plan change today:** the networking game (AI personas, hidden goals, rapport bar), the live voice
loop, online matches, Supabase and community judging are cut. Their docs are in
`docs/implementation/archive/`.

Stack: static `web/` page + a FastAPI server on localhost. ElevenLabs Scribe and Gemini are used
only inside the grader. No database, no accounts, no deploy.

## Where things stand

| Path | What it is | Tests on `main` |
| --- | --- | --- |
| `softskills-grader/` | Rates a spoken argument: pace, diction, tone, logic, emotion | `python softskills-grader/tests/test_pipeline.py`: 23/23 |
| `Arena/match_judge.py` | Duel verdicts with opening, rebuttal and clash; series across games | `python Arena/test_match_judge.py`: 27/27 |
| `web/` | Pixel-art home screen with an ARGUE button | - |

**Checked for this plan:** the turn order maps onto `judge_match("duel", ...)` with no code changes
(Player 2's speech is passed as both `opening` and `rebuttal`), and the Build Plan's server sketch
runs end to end against a stubbed grader (409 while grading, then a verdict).

**Not started:** `server/`, the match screens, the recorder. Nothing has been run against live
ElevenLabs or Gemini yet.

**Open pull request:** [#4 Retry busy API responses and fall back to another Gemini model](https://github.com/alenabhilash03-beep/WahoosHackNC2026/pull/4).
Merge it before Phase 1.

## What to do next

Follow the schedule in the Build Plan: Phase 0 foundations, Phase 1 rate one speech, Phase 2
two-player match, Phase 3 verdict and rematch, Phase 4 calibration (in parallel from hour 8),
Phase 5 demo. Steps for Phase 0 are in [docs/implementation/README.md](docs/implementation/README.md).

## Decisions

- **Scores come from code.** Gemini extracts with verbatim quotes; Python turns them into numbers.
- **One recording, one grade.** The grader caches by audio hash.
- **Keys stay on the server.** The browser only talks to localhost.
- **Scores stay hidden until the verdict.**
- **Going second has a duty.** Player 2's clash against Player 1's claims is 20% of their score.

## Known gaps

- The clash heuristic (2+ shared content words per claim) is untested on real transcripts.
- The repo is public: never commit `.env` or recordings; add `.gitignore` in Phase 0.
- `softskills-grader/README.md` says 22 checks; there are 23 on `main`.

## Run all tests

```bash
python softskills-grader/tests/test_pipeline.py && python Arena/test_match_judge.py
```
