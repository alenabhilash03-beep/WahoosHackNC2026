"""Tests for match_judge.py. Run: python Arena/test_match_judge.py

Part 1 uses hand-built reports in the grader's exact shape.
Part 2 (skipped if ../softskills-grader is missing) grades the grader's own test
speech at different speaking tempos and judges real reports.
"""
import copy
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from match_judge import (DEFAULT_MARGIN, Entry, clash, elo_multi, judge_match,  # noqa: E402
                         judge_series, rank_with_margin, Unit)

TOPIC, RUBRIC = "Should the frontier of AI be paced?", "rubric-1"
results = {}


def check(name, cond):
    results[name] = bool(cond)


def report(score, factors=None, topic=TOPIC, rubric=RUBRIC, gradable=True, claims=(), transcript=""):
    factors = factors or {"pace": score, "diction": score, "tone": score, "logic": score, "emotion": score}
    return {
        "gradable": gradable, "reason": None if gradable else "Only 12 words were spoken",
        "overall": {"score": score, "letter": "?"},
        "factors": {k: {"score": v, "base": v, "deducted": 0.0, "weight": 20} for k, v in factors.items()},
        "inputs": {"topic": topic, "standards_id": rubric, "side": "yes"},
        "evidence": {"argument": {"arguments": [{"claim": c, "status": "supported"} for c in claims]}},
        "transcript": transcript,
    }


def entry(player, score, side="yes", team=None, **kw):
    return Entry(player, side, {"opening": report(score, **kw)}, team)


# ---- duels --------------------------------------------------------------------
v = judge_match("duel", [entry("ana", 90), entry("ben", 80, "no")])
check("duel: clear gap wins on score", v["decision"] == "score" and v["placements"][0]["id"] == "ana")
check("duel: explanation names winner and gap", "ana wins by 10.0" in v["explanation"][0])

fa = {"pace": 80, "diction": 85, "tone": 80, "logic": 95, "emotion": 90}
fb = {"pace": 86, "diction": 85, "tone": 80, "logic": 85, "emotion": 84}
v = judge_match("duel", [entry("ana", 85.0, factors=fa), entry("ben", 84.0, "no", factors=fb)])
check("duel: inside margin goes to factors", v["decision"] == "factors" and v["placements"][0]["id"] == "ana")
check("duel: factor explanation lists the factors", "logic" in v["explanation"][0] and "emotion" in v["explanation"][0])

v = judge_match("duel", [entry("ana", 85.0), entry("ben", 83.5, "no")])
check("duel: inside margin, no factor lead = draw", v["decision"] == "draw"
      and v["placements"][0]["place"] == v["placements"][1]["place"] == 1)
check("duel: draw between equal ratings moves nobody", all(abs(d) < 1e-9 for d in v["rating_changes"].values()))

v = judge_match("duel", [entry("ana", 70), entry("ben", 0, "no", gradable=False)])
check("duel: ungradable speech scores 0 and loses", v["placements"][1]["id"] == "ben" and v["placements"][1]["score"] == 0)

for bad, kw in (("motion", {"topic": "Something else"}), ("rubric", {"rubric": "rubric-2"})):
    try:
        judge_match("duel", [entry("ana", 80), entry("ben", 70, "no", **kw)])
        check(f"duel: refuses mixed {bad}", False)
    except ValueError:
        check(f"duel: refuses mixed {bad}", True)

# ---- rebuttal and clash ----------------------------------------------------------
claims = ["pausing AI research protects workers from sudden job losses",
          "regulators need years to understand frontier systems"]
ben_open = report(80, claims=claims)
ana_reb = report(78, transcript="My opponent says workers face sudden job losses, but new jobs appear "
                                "faster than any pause could protect them.")
check("clash: one of two claims engaged = 50", clash(ana_reb, ben_open) == 50.0)
check("clash: no claims = not scored", clash(ana_reb, report(80)) is None)
ana = Entry("ana", "no", {"opening": report(82), "rebuttal": ana_reb})
ben = Entry("ben", "yes", {"opening": ben_open, "rebuttal": report(78, transcript="nothing relevant here at all")})
v = judge_match("duel", [ana, ben])
pa = next(p for p in v["placements"] if p["id"] == "ana")
expected = (0.5 * 82 + 0.3 * 78 + 0.2 * 50) / 1.0
check("rebuttal: weighted opening 0.5, rebuttal 0.3, clash 0.2", abs(pa["score"] - round(expected, 2)) < 0.01)

# ---- free-for-all ------------------------------------------------------------------
ffa = [entry(p, s) for p, s in (("ana", 92), ("ben", 90.5), ("cy", 85), ("dee", 84), ("eli", 70))]
v = judge_match("ffa", ffa)
places = {p["id"]: p["place"] for p in v["placements"]}
check("ffa: ties share a place, next place skips", places == {"ana": 1, "ben": 1, "cy": 3, "dee": 3, "eli": 5})
check("ffa: explanation marks ties with =", "1=. ana  92.0" in v["explanation"])
units = [Unit(i, [i], s, {}) for i, s in (("a", 92), ("b", 89.5), ("c", 87))]
places = {u.id: p for u, p in rank_with_margin(units, DEFAULT_MARGIN)}
check("ffa: margin measured from group top, no chaining", places == {"a": 1, "b": 1, "c": 3})
check("ffa: Elo is zero-sum", abs(sum(v["rating_changes"].values())) < 0.05)
check("ffa: winner gains, last loses", v["rating_changes"]["ana"] > 0 > v["rating_changes"]["eli"])
try:
    judge_match("ffa", ffa[:2])
    check("ffa: rejects 2 players", False)
except ValueError:
    check("ffa: rejects 2 players", True)

# ---- teams --------------------------------------------------------------------------
teams = [entry("ana", 88, team="red"), entry("ben", 80, team="red"),
         entry("cy", 85, "no", team="blue"), entry("dee", 84, "no", team="blue")]
v = judge_match("team", teams)
check("team: margin shrinks with team size (3 / sqrt 2)", abs(v["margin"] - round(DEFAULT_MARGIN / 2 ** 0.5, 2)) < 1e-9)
check("team: 84.0 vs 84.5 is inside the team margin", v["decision"] in ("draw", "factors"))
teams[1] = entry("ben", 70, team="red")
v = judge_match("team", teams)
check("team: clear gap, blue wins", v["placements"][0]["id"] == "blue" and v["decision"] == "score")
check("team: every member gets the team's rating change",
      v["rating_changes"]["cy"] == v["rating_changes"]["dee"] > 0 > v["rating_changes"]["ana"] == v["rating_changes"]["ben"])

# ---- ratings and series --------------------------------------------------------------
d = elo_multi([(Unit("a", ["a"], 0, {}), 1), (Unit("b", ["b"], 0, {}), 2)], {"a": 1400, "b": 1200})
check("elo: favourite gains less than an underdog would", 0 < d["a"] < 16)
g1 = judge_match("duel", [entry("ana", 86), entry("ben", 82, "no")])
g2 = judge_match("duel", [entry("ana", 80, "no"), entry("ben", 83)])
s = judge_series([g1, g2])
check("series: 166 vs 165 over two games is a draw", s["winner"] is None and abs(s["margin"] - 4.24) < 0.01)
check("deterministic", judge_match("ffa", ffa) == judge_match("ffa", copy.deepcopy(ffa)))

# ---- part 2: real grader reports ------------------------------------------------------
GRADER = os.path.join(HERE, "..", "softskills-grader")
if os.path.isdir(GRADER):
    import numpy as np
    from scipy.io import wavfile
    sys.path[:0] = [GRADER, os.path.join(GRADER, "tests")]
    import fixtures
    from grader import grade

    def graded_at(tempo, folder):
        words, _ = fixtures._timeline()
        for w in words:
            w["start"], w["end"] = round(w["start"] * tempo, 3), round(w["end"] * tempo, 3)
        signal, _ = fixtures._synthesise(words, words[-1]["end"] + 0.4)
        path = os.path.join(folder, f"tempo_{tempo}.wav")
        wavfile.write(path, fixtures.SR, np.round(np.clip(signal, -1, 1) * 32767).astype(np.int16))
        scribe = {"text": "", "words": [{"text": w["raw"], "start": w["start"], "end": w["end"],
                                         "type": "word", "logprob": w["logprob"]} for w in words]}
        return grade(path, fixtures.TOPIC, "yes", cache_dir=folder, transcript=scribe,
                     extraction=fixtures.GEMINI_ANNOTATIONS)

    with tempfile.TemporaryDirectory() as folder:
        normal, fast, slow = graded_at(1.0, folder), graded_at(0.875, folder), graded_at(1.05, folder)
    v = judge_match("duel", [Entry("normal", "yes", {"opening": normal}), Entry("fast", "no", {"opening": fast})])
    check("real reports: same words, 12.5% faster, loses by more than the margin",
          v["decision"] == "score" and v["placements"][0]["id"] == "normal")
    v = judge_match("duel", [Entry("normal", "yes", {"opening": normal}), Entry("slow", "no", {"opening": slow})])
    check("real reports: 5% slower is too close to call on score", v["decision"] in ("draw", "factors"))
    print(f"real reports: normal {normal['overall']['score']}, 12.5% faster {fast['overall']['score']}, "
          f"5% slower {slow['overall']['score']}")
else:
    print("softskills-grader not found next to Arena/: skipping part 2")

results = {k: v for k, v in results.items() if v is not None}
for name, ok in results.items():
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
passed = sum(results.values())
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
