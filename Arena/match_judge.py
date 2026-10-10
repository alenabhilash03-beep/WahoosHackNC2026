"""
NetworkMaxx Arena: machine-judged matches.

Modes
-----
    duel   1 v 1. Optional rebuttal round in which each player answers the other.
    team   2 v 2 or 3 v 3 (any number of teams). Team score = mean of member scores.
    ffa    free-for-all, 3 to 8 players, everyone ranked.

Input: softskills-grader reports (grader.grade(...) output), unchanged.
Output: placements, a plain-language explanation, and rating changes.
Pure functions, no network, deterministic.

Why a margin
------------
The grader buckets every metric (score ladder 100/85/65/40), so tiny differences
between two performances vanish, but a metric that crosses one bucket edge moves
the overall score by 0.4 to 4.9 points with the 2026-10-10.2 rubric, and the same
words spoken about 10% faster moved one test speech by 3.5 points. A gap smaller
than DEFAULT_MARGIN is therefore "too close to call" and goes to a factor-by-factor
tie-break, then to a draw. Re-measure with docs/implementation/phase-6 when the
rubric changes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

JUDGE_VERSION = "2026-10-10.1"
DEFAULT_MARGIN = 3.0          # overall points
FACTOR_LEAD = 5.0             # a factor counts as "won" only by this many points
ROUND_WEIGHTS = {"opening": 0.5, "rebuttal": 0.3, "clash": 0.2}
ELO_START = 1200.0
ELO_K = 32.0

STOPWORDS = set("""
a about after all also an and any are as at be because been but by can could did do does
for from had has have he her his how i if in into is it its just more most my no not now of
on one or our out so some than that the their them then there these they this those to too
up us very was we were what when which who why will with would you your should must
""".split())


# --------------------------------------------------------------------------- inputs
@dataclass
class Entry:
    """One player in a match. reports maps round name ("opening", "rebuttal") to a grader report."""
    player: str
    side: str
    reports: dict
    team: str | None = None


@dataclass
class Unit:
    """Something that gets a placement: a player (duel, ffa) or a team."""
    id: str
    members: list
    score: float
    factors: dict
    breakdown: dict = field(default_factory=dict)


def _overall(report: dict | None) -> float:
    if not report or not report.get("gradable", False) or not report.get("overall"):
        return 0.0
    return float(report["overall"]["score"])


def _factors(report: dict | None) -> dict:
    if not report or not report.get("gradable", False):
        return {}
    return {name: f["score"] for name, f in report.get("factors", {}).items() if f.get("score") is not None}


def check_comparable(entries: list) -> str:
    """All gradable reports must share one topic and one rubric. Returns the standards_id."""
    topics, rubrics = set(), set()
    for e in entries:
        for report in e.reports.values():
            if report and report.get("inputs"):
                topics.add(report["inputs"].get("topic"))
                rubrics.add(report["inputs"].get("standards_id"))
    if len(topics) > 1:
        raise ValueError(f"speeches were given different motions: {sorted(map(str, topics))}")
    if len(rubrics) > 1:
        raise ValueError("speeches were graded with different rubrics; regrade them from cache "
                         f"with one standards file first: {sorted(map(str, rubrics))}")
    return next(iter(rubrics), None)


# ---------------------------------------------------------------------------- clash
def _stems(text: str) -> set:
    out = set()
    for raw in text.lower().split():
        t = re.sub(r"[^a-z']", "", raw)
        if len(t) < 4 or t in STOPWORDS:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(t) > 5 and t.endswith(suffix):
                t = t[: -len(suffix)]
                break
        out.add(t)
    return out


def clash(rebuttal_report: dict | None, opponent_opening: dict | None, min_shared: int = 2) -> float | None:
    """Share of the opponent's verified claims that the rebuttal engages, 0-100.

    A claim counts as engaged when the rebuttal transcript shares at least `min_shared`
    content words with it. Heuristic: upgrade to a Gemini label with a verified quote
    per claim if it proves too loose.
    """
    if not rebuttal_report or not opponent_opening or not rebuttal_report.get("gradable"):
        return None
    claims = [a["claim"] for a in opponent_opening.get("evidence", {}).get("argument", {}).get("arguments", [])
              if a.get("claim")]
    if not claims:
        return None
    said = _stems(rebuttal_report.get("transcript", ""))
    engaged = sum(len(_stems(c) & said) >= min_shared for c in claims)
    return 100.0 * engaged / len(claims)


def player_score(entry: Entry, opponent: Entry | None = None) -> tuple:
    """Opening only: its overall score. With a rebuttal: weighted opening, rebuttal and clash."""
    opening = entry.reports.get("opening")
    rebuttal = entry.reports.get("rebuttal")
    open_score = _overall(opening)
    breakdown = {"opening": open_score}
    if rebuttal is None:
        return open_score, breakdown
    reb_score = _overall(rebuttal)
    clash_score = clash(rebuttal, opponent.reports.get("opening") if opponent else None)
    breakdown.update(rebuttal=reb_score, clash=clash_score)
    parts = [("opening", open_score), ("rebuttal", reb_score)]
    if clash_score is not None:
        parts.append(("clash", clash_score))
    total_w = sum(ROUND_WEIGHTS[k] for k, _ in parts)
    return sum(ROUND_WEIGHTS[k] * v for k, v in parts) / total_w, breakdown


# -------------------------------------------------------------------------- ranking
def rank_with_margin(units: list, margin: float) -> list:
    """Sort by score; a unit within `margin` of its group's TOP score shares that group's place.

    Measuring from the group top (not the previous unit) stops long chains of
    near-ties from merging a whole lobby into one place.
    Returns [(unit, place)], place 1 = best, tied units share the place.
    """
    ordered = sorted(units, key=lambda u: (-u.score, u.id))
    out, group_top, place = [], None, 0
    for i, u in enumerate(ordered):
        if group_top is None or group_top - u.score >= margin:
            group_top, place = u.score, i + 1
        out.append((u, place))
    return out


def factor_tiebreak(a: Unit, b: Unit, lead: float = FACTOR_LEAD) -> tuple:
    """Count factors each side leads by at least `lead` points."""
    wins_a = [f for f in a.factors if f in b.factors and a.factors[f] - b.factors[f] >= lead]
    wins_b = [f for f in b.factors if f in a.factors and b.factors[f] - a.factors[f] >= lead]
    return wins_a, wins_b


# --------------------------------------------------------------------------- ratings
def elo_multi(placements: list, ratings: dict, k: float = ELO_K) -> dict:
    """Pairwise Elo over every pair of units; a team's rating is its members' mean.

    placements: [(Unit, place)]. Each member receives the unit's change.
    Zero-sum across the match when teams are the same size.
    """
    def unit_rating(u):
        return sum(ratings.get(m, ELO_START) for m in u.members) / len(u.members)

    n = len(placements)
    if n < 2:
        return {m: 0.0 for u, _ in placements for m in u.members}
    deltas = {}
    for u, place in placements:
        r_u, change = unit_rating(u), 0.0
        for v, other_place in placements:
            if v is u:
                continue
            actual = 1.0 if place < other_place else 0.5 if place == other_place else 0.0
            expected = 1.0 / (1.0 + 10 ** ((unit_rating(v) - r_u) / 400.0))
            change += actual - expected
        for m in u.members:
            deltas[m] = round(k / (n - 1) * change, 2)
    return deltas


# ----------------------------------------------------------------------------- judge
def judge_match(mode: str, entries: list, ratings: dict | None = None,
                margin: float = DEFAULT_MARGIN) -> dict:
    """Judge one match. mode: "duel", "team" or "ffa"."""
    if mode not in ("duel", "team", "ffa"):
        raise ValueError("mode must be duel, team or ffa")
    if mode == "duel" and len(entries) != 2:
        raise ValueError("a duel needs exactly 2 entries")
    if mode == "ffa" and not 3 <= len(entries) <= 8:
        raise ValueError("free-for-all needs 3 to 8 entries")
    standards_id = check_comparable(entries)
    ratings = dict(ratings or {})

    # Opponent for clash: duel = the other player; team = first member of another team.
    def opponent_of(e: Entry):
        if mode == "duel":
            return next(o for o in entries if o is not e)
        if mode == "team":
            return next((o for o in entries if o.team != e.team), None)
        return None

    scored = {}
    for e in entries:
        s, br = player_score(e, opponent_of(e))
        scored[e.player] = (round(s, 2), br, _factors(e.reports.get("opening")))

    if mode == "team":
        teams = {}
        for e in entries:
            if e.team is None:
                raise ValueError(f"team mode: {e.player} has no team")
            teams.setdefault(e.team, []).append(e.player)
        units = []
        for t, members in sorted(teams.items()):
            mean = sum(scored[p][0] for p in members) / len(members)
            fac_names = set.intersection(*(set(scored[p][2]) for p in members)) if members else set()
            factors = {f: sum(scored[p][2][f] for p in members) / len(members) for f in fac_names}
            units.append(Unit(t, members, round(mean, 2), factors,
                              {p: scored[p][0] for p in members}))
        smallest = min(len(m) for m in teams.values())
        used_margin = margin / math.sqrt(smallest)       # a mean of n speeches wobbles less
    else:
        units = [Unit(p, [p], s, fac, br) for p, (s, br, fac) in sorted(scored.items())]
        used_margin = margin

    placements = rank_with_margin(units, used_margin)
    decision, lines = "score", []

    if len(units) == 2:
        (top, p1), (second, p2) = placements
        gap = top.score - second.score
        if p1 == p2:
            wins_a, wins_b = factor_tiebreak(top, second)
            if len(wins_a) != len(wins_b):
                winner, loser = (top, second) if len(wins_a) > len(wins_b) else (second, top)
                w_f, l_f = (wins_a, wins_b) if winner is top else (wins_b, wins_a)
                placements = [(winner, 1), (loser, 2)]
                decision = "factors"
                lines.append(f"Too close on overall ({gap:.1f} points, inside the {used_margin:.1f}-point margin). "
                             f"{winner.id} wins on factors {len(w_f)} to {len(l_f)}: led by {FACTOR_LEAD:g}+ points in "
                             f"{', '.join(w_f)}.")
            else:
                decision = "draw"
                lines.append(f"Draw: {gap:.1f} points apart, inside the {used_margin:.1f}-point margin, "
                             f"and factors split {len(wins_a)} to {len(wins_b)}.")
        else:
            lines.append(f"{top.id} wins by {gap:.1f} points ({top.score:.1f} to {second.score:.1f}).")
    else:
        for u, place in placements:
            tied = sum(1 for _, p in placements if p == place) > 1
            lines.append(f"{place}{'=' if tied else ''}. {u.id}  {u.score:.1f}")
        if any(sum(1 for _, p in placements if p == pl) > 1 for _, pl in placements):
            decision = "score_with_ties"

    deltas = elo_multi(placements, ratings)
    new_ratings = {m: round(ratings.get(m, ELO_START) + d, 2) for m, d in deltas.items()}
    return {
        "mode": mode, "judge_version": JUDGE_VERSION, "standards_id": standards_id,
        "margin": round(used_margin, 2), "decision": decision,
        "placements": [{"id": u.id, "members": u.members, "place": p, "score": u.score,
                        "factors": {k: round(v, 1) for k, v in sorted(u.factors.items())},
                        "breakdown": u.breakdown} for u, p in placements],
        "explanation": lines, "rating_changes": deltas, "new_ratings": new_ratings,
    }


def judge_series(game_verdicts: list, margin: float = DEFAULT_MARGIN) -> dict:
    """Ranked duel = 2 games with sides swapped. Sum each player's scores across games.

    The margin grows with the square root of the number of games summed.
    """
    totals = {}
    for v in game_verdicts:
        if v["mode"] != "duel":
            raise ValueError("series are for duels")
        for p in v["placements"]:
            totals[p["id"]] = totals.get(p["id"], 0.0) + p["score"]
    if len(totals) != 2:
        raise ValueError("a series must be between the same 2 players")
    (a, sa), (b, sb) = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    used = margin * math.sqrt(len(game_verdicts))
    gap = sa - sb
    winner = a if gap >= used else None
    return {"winner": winner, "totals": {a: round(sa, 2), b: round(sb, 2)}, "margin": round(used, 2),
            "explanation": (f"{a} wins the series by {gap:.1f} points." if winner else
                            f"Series drawn: {gap:.1f} points apart, inside the {used:.1f}-point margin.")}
