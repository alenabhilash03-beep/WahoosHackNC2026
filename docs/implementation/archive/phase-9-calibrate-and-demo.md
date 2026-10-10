# Phase 9 - Calibrate, seed, rehearse, submit

**Goal:** the numbers behave sensibly on real voices, the demo motions have settled crowd scores,
the team has rehearsed three times, and a backup video exists.

| | |
| --- | --- |
| Lanes | Everyone |
| Hours | 28 to 36; feature freeze at 33 |
| Needs | All earlier phases you are demoing |

## Done when

- [ ] Calibration: in at least 80% of cases, each teammate's deliberately good recording outscores
      their normal one, and their normal one outscores their flat one, for both conversation and debate
- [ ] `settings.match_margin` is set from the re-recording test below and noted in the plan
- [ ] Both demo motions have 6 or more published speeches, 4 gold pairs, and every demo speech is
      settled (20+ judgments)
- [ ] The anonymous sign-in rate limit is raised (Phase 0, step 1) and tested from one network with
      10 phones
- [ ] Three full rehearsals done; the backup video is recorded and plays offline
- [ ] All test suites pass on main at the freeze commit; the commit is tagged `demo`

## Steps

### 1. Calibration recordings (hours 28 to 30)

Each teammate records, with headphones, in a quiet room:

| Mode | Recording | How to perform it |
| --- | --- | --- |
| Coffee chat | good | Follow up on what Dana says, remember Lisbon, land your main point with a pause before it |
| Coffee chat | normal | Just have the conversation |
| Coffee chat | flat | Monotone, no questions, talk over her once |
| Debate | strong | Thesis, 3 supported reasons, address the other side, conclude |
| Debate | normal | Just argue |
| Debate | weak | Two unsupported claims, fillers, trail off |

Compare overall scores. When the order is wrong, read `report["measurements"]` (debate) or
`metrics` (conversation) to see which metric misfired, and change the YAML bucket or curve, not the
code. Every YAML change changes `standards_id`; re-run all test suites after each one.

Re-check the pitch tracker on 5 of these recordings: the built-in tracker's pitch spread should be
within 0.5 semitones of Praat's (`parselmouth`) on each. If it is not, switch to Praat as described
in the plan's grader comparison.

### 2. Measure the match margin (hour 30)

Each teammate records the **same** debate speech twice, a few minutes apart. Grade every recording
and compute the gaps:

```python
import numpy as np

pairs = [(r1["overall"]["score"], r2["overall"]["score"]) for r1, r2 in recordings_by_person]
gaps = np.abs([a - b for a, b in pairs])
margin = max(3.0, float(np.percentile(gaps, 95)))
print(f"median gap {np.median(gaps):.1f}, 95th percentile {np.percentile(gaps, 95):.1f} -> margin {margin:.1f}")
```

Set `MATCH_MARGIN` (read by `settings.match_margin`) to that value and redeploy. With 4 people
that is only 4 pairs, so treat it as a floor check: never set the margin below 3.0.

### 3. Seed the demo (hours 30 to 32)

1. Pick 2 motions for the demo. For each, publish the team's strong and weak debate speeches from
   step 1 (from a team account) and insert 4 `gold_pairs`.
2. Run `simulate_judges.py` only against a staging copy, never the demo database: bot judgments
   are for testing, not for the numbers shown on stage.
3. Ask 20 or more people at the venue to judge for 5 minutes each through a QR code to
   `/judge/<motion>`, until every demo speech shows "settled".
4. Pre-grade everything once so the grader cache is warm.

### 4. Demo script (3 minutes)

1. **0:00 to 0:20, hook.** Soft skills decide referrals and offers, and nobody gets feedback. We
   made practice a game where every point is measured.
2. **0:20 to 1:30, coffee chat with Dana.** Follow up on Lisbon (bar jumps, "Great!"), make the ask
   too early on purpose ("Blunder??", Dana deflects), recover, ask again, win. Card reveal; click a
   red marker.
3. **1:30 to 2:30, live duel.** Two teammates play a casual duel on a demo motion. While they
   speak, show the QR code: the room judges two prepared speeches and the crowd column moves on the
   big screen. Verdict reveal, with its plain-language reason.
4. **2:30 to 3:00, credibility.** Re-grade one recording twice on screen: identical report. Show
   the margin rule ("a win only counts when it beats what one bucket can swing") and the science table.

### 5. Fallbacks

| If this fails | Do this |
| --- | --- |
| Venue Wi-Fi | Phone hotspot for the demo laptop; the backup video for everything else |
| ElevenLabs or Gemini outage | Backup video; then show a stored card and stored match verdict from the database |
| Crowd too slow to judge | Show the seeded, settled crowd scores; say how many people judged |
| Live duel overruns | Skip to a stored verdict of an earlier duel |

### 6. Freeze and submit (hours 33 to 36)

1. At hour 33 merge only fixes. Run every test suite; tag the commit `demo`.
2. Record the backup video of the full demo script on the deployed site.
3. Update the plan's numbers (margin, calibration results) and the repo README.
4. Submit, then rehearse twice more.
