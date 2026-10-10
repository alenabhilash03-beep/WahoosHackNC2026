# WahoosHackNC2026

## Speech grader

The spoken-argument grader lives in [`softskills-grader/`](softskills-grader/README.md). The research behind its grading standards is summarised in [Research basis for the standards](softskills-grader/README.md#research-basis-for-the-standards).

## Building NetworkMaxx

- [Implementation guide](docs/implementation/README.md): the build plan as executable phases, from setup to demo.
- [`Arena/`](Arena/): community rating aggregation (`arena_ratings.py`) and the machine judge for duels, team matches and free-for-all (`match_judge.py`).
- [`conversation/`](conversation/): the conversation scorer that Phase 3 merges into the grader.
- [`supabase/migrations/`](supabase/migrations/): the database schema for every phase.
