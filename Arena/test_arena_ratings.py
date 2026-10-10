"""Simulated community with known truth: does aggregation recover it and resist bias and spam?"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arena_ratings import (Judgment, Speech, aggregate_motion, fit_bradley_terry,  # noqa: E402
                           rater_weights, wilson_lower_bound)

rng = np.random.default_rng(1)
N_SPEECH, N_HONEST, N_SPAM, PAIRS_EACH, TRUE_BIAS, YES_SHARE = 30, 150, 20, 25, 1.0, 0.7

speeches = [Speech(f"s{i}", f"p{i}", "pace_ai", "yes" if i < N_SPEECH // 2 else "no") for i in range(N_SPEECH)]
true_logos = {s.id: float(rng.normal()) for s in speeches}
true_pathos = {s.id: float(rng.normal()) for s in speeches}
true_persuade = {s.id: float(rng.normal()) for s in speeches}
yes_ids = [s.id for s in speeches if s.side == "yes"]
no_ids = [s.id for s in speeches if s.side == "no"]
side = {s.id: s.side for s in speeches}
sig = lambda x: 1 / (1 + math.exp(-x))  # noqa: E731

# Gold pairs: 4 distinct cross-side pairs of a clearly strong vs a clearly weak speech.
ys, ns = sorted(yes_ids, key=true_logos.get), sorted(no_ids, key=true_logos.get)
gold = {(ys[-1], ns[0]): "a", (ys[-2], ns[1]): "a", (ns[-1], ys[0]): "a", (ns[-2], ys[1]): "a"}
gold_unordered = {frozenset(k) for k in gold}
cross_pairs = [(y, n) for y in yes_ids for n in no_ids if frozenset((y, n)) not in gold_unordered]


def honest_pick(a, b, truth, held):
    s = lambda sid: 1.0 if side[sid] == held else 0.0  # noqa: E731
    return "a" if rng.random() < sig(truth[a] - truth[b] + TRUE_BIAS * (s(a) - s(b))) else "b"


judgments = []
for r in range(N_HONEST + N_SPAM):
    rater, spam = f"r{r}", r >= N_HONEST
    picks = rng.choice(len(cross_pairs), size=PAIRS_EACH, replace=False)
    for (a, b) in list(gold) + [cross_pairs[k] for k in picks]:
        if rng.random() < 0.5 and (a, b) not in gold:
            a, b = b, a
        pre = int(rng.choice([1, 2])) if rng.random() < YES_SHARE else int(rng.choice([-1, -2]))
        held = "yes" if pre > 0 else "no"
        if spam:
            lp, pp = rng.choice(["a", "b"]), rng.choice(["a", "b"])
            post, ta, tb = int(rng.integers(-2, 3)), bool(rng.random() < 0.5), bool(rng.random() < 0.5)
        else:
            lp, pp = honest_pick(a, b, true_logos, held), honest_pick(a, b, true_pathos, held)
            yes_sp, no_sp = (a, b) if side[a] == "yes" else (b, a)
            p_toward_yes = sig(true_persuade[yes_sp] - true_persuade[no_sp])
            move = 0 if rng.random() < 0.5 else (1 if rng.random() < p_toward_yes else -1)
            post = max(-2, min(2, pre + move))
            ta, tb = bool(rng.random() < sig(true_logos[a])), bool(rng.random() < sig(true_logos[b]))
        judgments.append(Judgment(rater, a, b, pre, post, lp, pp, ta, tb))

# Noise the aggregator must drop: self-rating, duplicate, did not listen.
judgments.append(Judgment("p0", "s0", "s20", 1, 2, "a", "a", True, False))
judgments.append(judgments[10])
judgments.append(Judgment("r999", "s1", "s21", 1, 2, "a", "a", True, True, listened_fully=False))

result = aggregate_motion(speeches, judgments, gold=gold)
res = result["speeches"]


def spearman(x, y):
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


ids = [s.id for s in speeches]
rho_logos = spearman([res[i]["logos"] for i in ids], [true_logos[i] for i in ids])
rho_pathos = spearman([res[i]["pathos"] for i in ids], [true_pathos[i] for i in ids])
rho_persuade = spearman([res[i]["persuasion"] for i in ids], [true_persuade[i] for i in ids])
rho_avg_shift = spearman([res[i]["avg_shift"] for i in ids], [true_persuade[i] for i in ids])

# Same data without the side term: yes-side speeches should be inflated (70% of raters lean yes).
rows = []
by_id = {s.id: s for s in speeches}
for j in judgments:
    if (j.a, j.b) in gold or j.rater.startswith("p") or not j.listened_fully:
        continue
    win, lose = (j.a, j.b) if j.logos_pick == "a" else (j.b, j.a)
    rows.append((win, lose, 0.0, 0.0))
naive, _ = fit_bradley_terry(rows, sorted(ids), side_term=False)
true_gap = np.mean([true_logos[i] for i in yes_ids]) - np.mean([true_logos[i] for i in no_ids])
naive_gap = np.mean([naive[i] for i in yes_ids]) - np.mean([naive[i] for i in no_ids])
pct_gap = np.mean([res[i]["logos"] for i in yes_ids]) - np.mean([res[i]["logos"] for i in no_ids])
naive_pct = {i: v for i, v in zip(sorted(ids), np.argsort(np.argsort([naive[i] for i in sorted(ids)])) / (N_SPEECH - 1) * 100)}
naive_pct_gap = np.mean([naive_pct[i] for i in yes_ids]) - np.mean([naive_pct[i] for i in no_ids])
true_pct = {i: v for i, v in zip(ids, np.argsort(np.argsort([true_logos[i] for i in ids])) / (N_SPEECH - 1) * 100)}
true_pct_gap = np.mean([true_pct[i] for i in yes_ids]) - np.mean([true_pct[i] for i in no_ids])

elo = result["elo"]
w = rater_weights(judgments, gold)
honest_w = np.mean([w[f"r{r}"] for r in range(N_HONEST)])
spam_w = np.mean([w[f"r{r}"] for r in range(N_HONEST, N_HONEST + N_SPAM)])
checks = {
    "logos ranking recovers truth (Spearman > 0.85)": rho_logos > 0.85,
    "pathos ranking recovers truth (Spearman > 0.85)": rho_pathos > 0.85,
    "persuasion ranking tracks truth (Spearman > 0.8)": rho_persuade > 0.8,
    "head-to-head persuasion beats raw average shift": rho_persuade > rho_avg_shift,
    "side bias estimated near true 1.0 (within 0.3)": abs(result["side_bias"]["logos"] - TRUE_BIAS) < 0.3,
    "correction shrinks the yes-side inflation": abs(pct_gap - true_pct_gap) < abs(naive_pct_gap - true_pct_gap),
    "self-rating, duplicate, unlistened all dropped": result["dropped_judgments"] == 3,
    "spam raters weigh less than honest raters": spam_w < honest_w,
    "Elo is zero-sum": abs(sum(elo.values()) - 1200 * len(elo)) < 1e-6 * len(elo) + 0.5,
    "Wilson: 2/2 ranks below 95/100": wilson_lower_bound(2, 2) < wilson_lower_bound(95, 100),
    "Wilson: no votes = 0": wilson_lower_bound(0, 0) == 0.0,
    "no speech is provisional at this volume": not any(r["provisional"] for r in res.values()),
    "deterministic rerun": aggregate_motion(speeches, judgments, gold=gold) == result,
}

print(f"Spearman logos {rho_logos:.3f}  pathos {rho_pathos:.3f}  persuasion {rho_persuade:.3f}"
      f"  (raw avg shift {rho_avg_shift:.3f})")
print(f"side bias estimate logos {result['side_bias']['logos']}  pathos {result['side_bias']['pathos']}  (true {TRUE_BIAS})")
print(f"yes-minus-no percentile gap: true {true_pct_gap:.1f}  corrected {pct_gap:.1f}  uncorrected {naive_pct_gap:.1f}")
print(f"used {result['used_judgments']} judgments, dropped {result['dropped_judgments']}")
print(f"mean rater weight: honest {honest_w:.2f}  spam {spam_w:.2f}")
for k, v in checks.items():
    print(f"  {'PASS' if v else 'FAIL'}  {k}")
sys.exit(0 if all(checks.values()) else 1)
