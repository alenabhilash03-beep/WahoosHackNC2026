# WahoosHackNC2026

## Debate Arena

A speech and rhetoric game for two players on one laptop: Player 1 argues a motion, Player 2 goes
after and answers them, every speech is rated, and the game names a winner.

- [Build plan](Build%20Plan.md): the game, scoring, architecture, schedule and demo.
- [`softskills-grader/`](softskills-grader/README.md): rates each speech (pace, diction, tone,
  logic, emotion). The research behind its standards is in
  [Research basis for the standards](softskills-grader/README.md#research-basis-for-the-standards).
- [`Arena/match_judge.py`](Arena/match_judge.py): picks the winner of a match and a series from the
  grader's reports.
- [`web/`](web/README.md): the pixel-art home screen.

The earlier NetworkMaxx plan (networking personas, rapport, online matches, community judging) is
archived in [`docs/implementation/archive/`](docs/implementation/archive/).
