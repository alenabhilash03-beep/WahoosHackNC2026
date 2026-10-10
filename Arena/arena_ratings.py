"""
NetworkMaxx Debate Arena: community rating aggregation.

Pure functions, numpy only, deterministic (fixed iteration counts, no randomness).

What it turns into numbers
--------------------------
Each judgment comes from one listener who heard two speeches on the same motion,
one per side, and recorded:
    pre_stance, post_stance   -2..+2 on the motion (-2 strongly no, +2 strongly yes)
    logos_pick                "a" or "b": better reasoning
    pathos_pick               "a" or "b": more moving
    trust_a, trust_b          True/False: would trust this speaker to brief them

Outputs per speech
------------------
    logos, pathos   Bradley-Terry strengths with a "my side" bias term removed,
                    reported as 0-100 percentiles within the motion
    ethos           Wilson lower bound of the share of listeners who trust them
    persuasion      Bradley-Terry strength from head-to-head stance moves (listener moved
                    toward this speech's side = a win), as a 0-100 percentile
    avg_shift       raw mean stance shift toward the speaker's side, shrunk toward 0
    provisional     True until the speech has MIN_COMPARISONS judgments
    settled         True once it has SETTLED_COMPARISONS judgments
Per player: debate Elo from head-to-head persuasion results.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

MIN_COMPARISONS = 10         # "provisional" below this; simulation: 5 -> rank corr 0.60, 10 -> 0.75
SETTLED_COMPARISONS = 20     # "settled" at or above this; simulation: 20 -> 0.84, 40 -> 0.89
PRIOR_SD = 1.0               # Gaussian prior on strengths: shrinks speeches with few votes
PERSUASION_PRIOR_N = 5       # pseudo-listeners at zero shift
ELO_K = 32
WILSON_Z = 1.96


@dataclass(frozen=True)
class Speech:
    id: str
    player: str
    motion: str
    side: str                # "yes" or "no"


@dataclass(frozen=True)
class Judgment:
    rater: str
    a: str                   # speech id heard first
    b: str
    pre_stance: int          # -2..+2
    post_stance: int
    logos_pick: str          # "a" or "b"
    pathos_pick: str
    trust_a: bool
    trust_b: bool
    listened_fully: bool = True


def side_of_stance(stance: int) -> str | None:
    return "yes" if stance > 0 else "no" if stance < 0 else None


# ----------------------------------------------------------------------------
# Bradley-Terry with a same-side bias term
#   P(i beats j) = sigmoid(theta_i - theta_j + beta * (s_i - s_j))
#   s = 1 if the speech argues the side the rater held BEFORE listening, else 0
# beta absorbs "I prefer arguments I already agree with"; theta is what is left.
# Fit: maximum a posteriori with a N(0, PRIOR_SD^2) prior, Newton steps.
# ----------------------------------------------------------------------------
def fit_bradley_terry(comparisons, ids, weights=None, side_term=True, iters=60):
    """comparisons: list of (winner_id, loser_id, s_winner, s_loser)."""
    index = {sid: k for k, sid in enumerate(ids)}
    n = len(ids)
    p = n + (1 if side_term else 0)
    params = np.zeros(p)
    if not comparisons:
        return dict(zip(ids, params[:n])), 0.0
    w = np.ones(len(comparisons)) if weights is None else np.asarray(weights, float)
    rows = np.zeros((len(comparisons), p))
    for r, (win, lose, s_win, s_lose) in enumerate(comparisons):
        rows[r, index[win]] = 1.0
        rows[r, index[lose]] = -1.0
        if side_term:
            rows[r, n] = s_win - s_lose
    prior = np.full(p, 1.0 / PRIOR_SD ** 2)
    if side_term:
        prior[n] = 1e-3                      # nearly flat prior on the bias term
    for _ in range(iters):
        z = rows @ params
        prob = 1.0 / (1.0 + np.exp(-z))      # probability the recorded winner wins
        grad = rows.T @ (w * (1.0 - prob)) - prior * params
        hess = -(rows.T * (w * prob * (1.0 - prob))) @ rows - np.diag(prior)
        step = np.linalg.solve(hess, grad)
        params = params - step
        if np.max(np.abs(step)) < 1e-9:
            break
    beta = float(params[n]) if side_term else 0.0
    return {sid: float(params[index[sid]]) for sid in ids}, beta


def percentile_within(values: dict) -> dict:
    """0-100 rank percentile; ties share the mean rank."""
    ids = sorted(values, key=lambda k: (values[k], k))
    out, i = {}, 0
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and math.isclose(values[ids[j + 1]], values[ids[i]]):
            j += 1
        pct = 100.0 * ((i + j) / 2) / max(len(ids) - 1, 1)
        for k in ids[i:j + 1]:
            out[k] = round(pct, 1)
        i = j + 1
    return out


def wilson_lower_bound(positive: int, total: int, z: float = WILSON_Z) -> float:
    if total == 0:
        return 0.0
    phat = positive / total
    denom = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total)
    return (centre - margin) / denom


def elo_update(r_a: float, r_b: float, score_a: float, k: float = ELO_K):
    expected_a = 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))
    return r_a + k * (score_a - expected_a), r_b + k * ((1 - score_a) - (1 - expected_a))


# ----------------------------------------------------------------------------
# Rater weights from gold pairs: matchups where the team knows which speech is
# better (one recorded deliberately strong, one deliberately weak).
# ----------------------------------------------------------------------------
def rater_weights(judgments, gold: dict, floor: float = 0.2, min_gold: int = 3) -> dict:
    """gold: {(a_id, b_id): "a" or "b"} expected logos pick. Weight = gold accuracy, floored."""
    hits, seen = defaultdict(int), defaultdict(int)
    for j in judgments:
        key = (j.a, j.b)
        if key in gold:
            seen[j.rater] += 1
            hits[j.rater] += int(j.logos_pick == gold[key])
    weights = {}
    for rater in {j.rater for j in judgments}:
        if seen[rater] >= min_gold:
            weights[rater] = max(floor, hits[rater] / seen[rater])
        else:
            weights[rater] = 0.5                 # unproven rater counts half
    return weights


# ----------------------------------------------------------------------------
# Main aggregation for one motion
# ----------------------------------------------------------------------------
def aggregate_motion(speeches: list[Speech], judgments: list[Judgment], gold: dict | None = None,
                     elo: dict | None = None) -> dict:
    by_id = {s.id: s for s in speeches}
    ids = sorted(by_id)
    valid = [j for j in judgments
             if j.listened_fully and j.a in by_id and j.b in by_id
             and by_id[j.a].side != by_id[j.b].side
             and j.rater not in (by_id[j.a].player, by_id[j.b].player)]
    # one judgment per rater per pair: keep the first
    seen, dedup = set(), []
    for j in valid:
        key = (j.rater, frozenset((j.a, j.b)))
        if key not in seen:
            seen.add(key)
            dedup.append(j)
    gold_pairs = set(gold or {})
    scored = [j for j in dedup if (j.a, j.b) not in gold_pairs]
    weights = rater_weights(dedup, gold or {})

    def comparisons(pick_field):
        rows, w = [], []
        for j in scored:
            pick = getattr(j, pick_field)
            win, lose = (j.a, j.b) if pick == "a" else (j.b, j.a)
            held = side_of_stance(j.pre_stance)
            s = lambda sid: 1.0 if held and by_id[sid].side == held else 0.0  # noqa: E731
            rows.append((win, lose, s(win), s(lose)))
            w.append(weights[j.rater])
        return rows, w

    def persuasion_comparisons():
        """A listener who moved toward one side = a head-to-head win for that side's speech."""
        rows, w = [], []
        for j in scored:
            move = j.post_stance - j.pre_stance
            if move == 0:
                continue
            toward = "yes" if move > 0 else "no"
            win, lose = (j.a, j.b) if by_id[j.a].side == toward else (j.b, j.a)
            held = side_of_stance(j.pre_stance)
            s = lambda sid: 1.0 if held and by_id[sid].side == held else 0.0  # noqa: E731
            rows.append((win, lose, s(win), s(lose)))
            w.append(weights[j.rater])
        return rows, w

    logos_rows, logos_w = comparisons("logos_pick")
    pathos_rows, pathos_w = comparisons("pathos_pick")
    pers_rows, pers_w = persuasion_comparisons()
    logos_theta, logos_bias = fit_bradley_terry(logos_rows, ids, logos_w)
    pathos_theta, pathos_bias = fit_bradley_terry(pathos_rows, ids, pathos_w)
    pers_theta, pers_bias = fit_bradley_terry(pers_rows, ids, pers_w)
    logos_pct, pathos_pct = percentile_within(logos_theta), percentile_within(pathos_theta)
    pers_pct = percentile_within(pers_theta)

    trust_yes, trust_n = defaultdict(int), defaultdict(int)
    shift_sum, shift_w, count = defaultdict(float), defaultdict(float), defaultdict(int)
    for j in scored:
        for sid, trusted in ((j.a, j.trust_a), (j.b, j.trust_b)):
            trust_n[sid] += 1
            trust_yes[sid] += int(trusted)
            count[sid] += 1
            toward = (j.post_stance - j.pre_stance) * (1 if by_id[sid].side == "yes" else -1)
            shift_sum[sid] += toward * weights[j.rater]
            shift_w[sid] += weights[j.rater]

    # Head-to-head persuasion -> Elo. Winner of a matchup = side the rater moved toward.
    ratings = dict(elo or {})
    for j in scored:
        pa, pb = by_id[j.a].player, by_id[j.b].player
        ratings.setdefault(pa, 1200.0)
        ratings.setdefault(pb, 1200.0)
        move = j.post_stance - j.pre_stance
        if move == 0:
            continue
        a_side_yes = by_id[j.a].side == "yes"
        a_won = (move > 0) == a_side_yes
        ratings[pa], ratings[pb] = elo_update(ratings[pa], ratings[pb], 1.0 if a_won else 0.0)

    results = {}
    for sid in ids:
        results[sid] = {
            "logos": logos_pct[sid], "pathos": pathos_pct[sid],
            "ethos": round(100 * wilson_lower_bound(trust_yes[sid], trust_n[sid]), 1),
            "persuasion": pers_pct[sid],
            "avg_shift": round(shift_sum[sid] / (shift_w[sid] + PERSUASION_PRIOR_N), 3),
            "judgments": count[sid],
            "provisional": count[sid] < MIN_COMPARISONS,
            "settled": count[sid] >= SETTLED_COMPARISONS,
        }
    return {"speeches": results, "side_bias": {"logos": round(logos_bias, 3), "pathos": round(pathos_bias, 3),
                                               "persuasion": round(pers_bias, 3)},
            "elo": {k: round(v, 1) for k, v in sorted(ratings.items())},
            "used_judgments": len(scored), "dropped_judgments": len(judgments) - len(dedup)}
