# SpeakUp

A HackNC 2026 project by team Wahoos.

Rates how well you argue out loud. Stage 1 is single player: pick a prompt and a side, speak for 60
seconds, and get a speech card with the three changes worth the most points. Stage 2 is community:
a prompt of the day, a leaderboard of everyone's graded answers, and community members rating each
other's speeches head to head.

**Try it:** https://wahoos-debate-arena.fly.dev (a temporary address; stage 1 only).

- [Build plan](Build%20Plan.md): both stages, scoring, architecture, schedule and demo.
- [`softskills-grader/`](softskills-grader/README.md): rates each speech (pace, diction, tone,
  logic, emotion). The research behind its standards is in
  [Research basis for the standards](softskills-grader/README.md#research-basis-for-the-standards).
- [`Arena/arena_ratings.py`](Arena/arena_ratings.py): turns community votes into crowd scores.
- [`supabase/migrations/`](supabase/migrations/): the database schema.
- [`web/`](web/README.md): the pixel-art home screen.

The two-player duel is parked (see the end of the Build Plan; `Arena/match_judge.py` scores it).
The earlier NetworkMaxx plan is archived in [`docs/implementation/archive/`](docs/implementation/archive/).
