# SpeakUp

A HackNC 2026 project by team Wahoos.

Rates how well you argue out loud. Stage 1 is single player: pick a prompt and a side, speak for 60
seconds, and get a speech card with the three changes worth the most points. Stage 2 is community:
a prompt of the day, a leaderboard of everyone's graded answers, and community members rating each
other's speeches head to head.

**Try it:** https://speak-up.fly.dev (sign in with GitHub). Live today: solo practice, the speech
card, progress and a grade leaderboard. Community rating is not built.

- [Build plan](Build%20Plan.md): both stages, scoring, architecture, schedule and demo.
- [`softskills-grader/`](softskills-grader/README.md): rates each speech (pace, diction, tone,
  logic, emotion). The research behind its standards is in
  [Research basis for the standards](softskills-grader/README.md#research-basis-for-the-standards).
- [`Arena/arena_ratings.py`](Arena/arena_ratings.py): turns community votes into crowd scores.
- [`supabase/migrations/`](supabase/migrations/): the database schema.
- [Database setup and verification](supabase/README.md): local Supabase, migration upgrades,
  RLS/private Storage checks, and concurrent display-name protection.
- [`web/`](web/README.md): the static site (home, practice, speech card, progress, leaderboard,
  login), served by the API server and, as a copy that calls the same API, by GitHub Pages.
- [`server/`](server/README.md): the FastAPI server and how it is deployed.
- [GitHub accounts](docs/implementation/social-accounts.md): Supabase
  OAuth app registration, login, and deployment setup; no SMTP needed.

The two-player duel is parked (see the end of the Build Plan; `Arena/match_judge.py` scores it).
The earlier NetworkMaxx plan is archived in [`docs/implementation/archive/`](docs/implementation/archive/).
