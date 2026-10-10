"""Phase 6 runner, exactly as written in the guide, driven through whole matches with an
in-memory Supabase stand-in and a fake clock. No network."""
import asyncio
import copy
import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.routes import matches as routes
from app.workers import match_runner as runner

SAMPLE = json.loads((Path(__file__).resolve().parents[2] / "softskills-grader" / "sample_report.json").read_text())
PK = {"matches": ("id",), "match_players": ("match_id", "player_id"), "match_speeches": ("match_id", "player_id", "round"),
      "match_results": ("match_id",), "ratings": ("player_id", "mode"), "speeches": ("id",), "motions": ("id",),
      "profiles": ("id",)}
DEFAULTS = {"matches": {"status": "lobby", "game": 1, "series_id": None, "phase_ends_at": None, "motion_id": None,
                        "standards_id": None, "ranked": False, "rebuttal": False},
            "match_players": {"forfeited": False, "team": None, "side": None},
            "speeches": {"report": None}, "ratings": {"rating": 1200, "games": 0}}
ids = itertools.count(1)


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload, self.order_by, self.lim = db, table, [], "select", None, None, None

    def select(self, *_):
        return self

    def eq(self, k, v):
        self.filters.append(lambda r, k=k, v=v: r.get(k) == v); return self

    def in_(self, k, vs):
        self.filters.append(lambda r, k=k, vs=vs: r.get(k) in vs); return self

    def order(self, col):
        self.order_by = col; return self

    def limit(self, n):
        self.lim = n; return self

    def update(self, data):
        self.op, self.payload = "update", data; return self

    def insert(self, data):
        self.op, self.payload = "insert", data; return self

    def upsert(self, data, **_):
        self.op, self.payload = "upsert", data; return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self.op in ("insert", "upsert"):
            out = []
            for item in (self.payload if isinstance(self.payload, list) else [self.payload]):
                row = {**DEFAULTS.get(self.table, {}), **item}
                if "id" in PK.get(self.table, ()) and "id" not in row:
                    row["id"] = f"{self.table}-{next(ids)}"
                key = tuple(row.get(k) for k in PK.get(self.table, ("id",)))
                existing = next((r for r in rows if tuple(r.get(k) for k in PK.get(self.table, ("id",))) == key), None)
                if existing is not None:
                    if self.op == "insert":
                        raise RuntimeError(f"duplicate key in {self.table}: {key}")
                    existing.update(item); out.append(copy.deepcopy(existing))
                else:
                    rows.append(row); out.append(copy.deepcopy(row))
            return type("R", (), {"data": out})
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return type("R", (), {"data": copy.deepcopy(hit)})
        if self.order_by:
            hit = sorted(hit, key=lambda r: r.get(self.order_by))
        if self.lim:
            hit = hit[: self.lim]
        return type("R", (), {"data": copy.deepcopy(hit)})


class FakeSupabase:
    def __init__(self):
        self.db = {}

    def table(self, name):
        return Query(self.db, name)


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def world(monkeypatch):
    fake, clock, events = FakeSupabase(), Clock(), []

    async def fake_broadcast(topic, event, payload):
        events.append((topic, event, payload))

    def fake_one(table, **eq):
        q = fake.table(table).select("*")
        for k, v in eq.items():
            q = q.eq(k, v)
        rows = q.limit(1).execute().data
        return rows[0] if rows else None

    for mod in (runner, routes):
        monkeypatch.setattr(mod, "supabase", fake)
        monkeypatch.setattr(mod, "one", fake_one)
        monkeypatch.setattr(mod, "broadcast", fake_broadcast)
        monkeypatch.setattr(mod, "now", clock)
    monkeypatch.setattr(runner.storage, "signed_url", lambda path, seconds=3600: f"signed:{path}")
    fake.table("motions").insert({"id": "m1", "text": SAMPLE["inputs"]["topic"], "active": True}).execute()
    return fake, clock, events


def report(score):
    r = copy.deepcopy(SAMPLE)
    r["overall"]["score"] = score
    for f in r["factors"].values():
        f["score"] = score
    return r


def make_match(fake, players, **kw):
    m = fake.table("matches").insert({"code": "ABCDEF", "created_by": players[0], **kw}).execute().data[0]
    for seat, p in enumerate(players):
        fake.table("profiles").upsert({"id": p, "display_name": p}).execute()
        fake.table("match_players").insert({"match_id": m["id"], "player_id": p, "seat": seat}).execute()
    return m


def speak(fake, match_id, player, round_, score):
    me = fake.table("match_players").select("*").eq("match_id", match_id).eq("player_id", player).execute().data[0]
    sp = fake.table("speeches").insert({"player_id": player, "motion_id": "m1", "side": me["side"], "round": round_,
                                        "audio_path": f"{player}.{round_}.webm", "report": report(score)}).execute().data[0]
    fake.table("match_speeches").insert({"match_id": match_id, "player_id": player, "round": round_,
                                         "speech_id": sp["id"]}).execute()


def run(coro):
    return asyncio.run(coro)


def status(fake, match_id):
    return fake.table("matches").select("*").eq("id", match_id).execute().data[0]["status"]


def pass_phase(clock, seconds):
    clock.advance(seconds + runner.GRACE_S + 1)
    run(runner.tick())


def test_ffa_runs_end_to_end_and_forfeits_the_silent_player(world):
    fake, clock, events = world
    m = make_match(fake, ["ana", "ben", "cy", "dee"], mode="ffa", size=4)
    run(routes.start_match(fake.table("matches").select("*").eq("id", m["id"]).execute().data[0]))
    assert status(fake, m["id"]) == "prep"
    sides = [p["side"] for p in fake.table("match_players").select("*").eq("match_id", m["id"]).execute().data]
    assert sorted(sides) == ["no", "no", "yes", "yes"]                       # balanced
    pass_phase(clock, 60)
    assert status(fake, m["id"]) == "speaking"
    for p, s in (("ana", 92), ("ben", 85), ("cy", 70)):                       # dee never uploads
        speak(fake, m["id"], p, "opening", s)
    pass_phase(clock, 60)
    assert status(fake, m["id"]) == "done"                                    # all graded: judged at once
    verdict = fake.table("match_results").select("*").eq("match_id", m["id"]).execute().data[0]["verdict"]
    places = {p["id"]: (p["place"], p["score"]) for p in verdict["placements"]}
    assert places["ana"][0] == 1 and places["dee"] == (4, 0.0)
    dee = fake.table("match_players").select("*").eq("player_id", "dee").execute().data[0]
    assert dee["forfeited"] is True
    assert any(e[1] == "verdict" for e in events)


def test_runner_never_judges_twice(world):
    fake, clock, _ = world
    m = make_match(fake, ["ana", "ben"], mode="duel", size=2)
    run(routes.start_match(fake.table("matches").select("*").eq("id", m["id"]).execute().data[0]))
    pass_phase(clock, 60)
    speak(fake, m["id"], "ana", "opening", 90)
    speak(fake, m["id"], "ben", "opening", 80)
    row = fake.table("matches").select("*").eq("id", m["id"]).execute().data[0]
    clock.advance(60 + runner.GRACE_S + 1)
    run(runner.advance(row))
    run(runner.advance(row))                                                  # stale copy: must be a no-op
    run(runner.finalize(m["id"]))
    run(runner.finalize(m["id"]))
    assert status(fake, m["id"]) == "done"
    assert len(fake.table("match_results").select("*").execute().data) == 1


def test_rebuttal_duel_and_missing_rebuttal_scores_zero(world):
    fake, clock, _ = world
    m = make_match(fake, ["ana", "ben"], mode="duel", size=2, rebuttal=True)
    run(routes.start_match(fake.table("matches").select("*").eq("id", m["id"]).execute().data[0]))
    pass_phase(clock, 60)
    speak(fake, m["id"], "ana", "opening", 85)
    speak(fake, m["id"], "ben", "opening", 85)
    pass_phase(clock, 60)
    assert status(fake, m["id"]) == "rebuttal_listen"
    state = runner.match_state(m["id"], "ana")
    assert [o["player_id"] for o in state["opponent_openings"]] == ["ben"]  # only the other side
    pass_phase(clock, 75)
    assert status(fake, m["id"]) == "rebuttal"
    speak(fake, m["id"], "ana", "rebuttal", 80)                               # ben skips his rebuttal
    pass_phase(clock, 30)
    assert status(fake, m["id"]) == "done"
    v = fake.table("match_results").select("*").execute().data[0]["verdict"]
    assert v["placements"][0]["id"] == "ana"
    ben = next(p for p in v["placements"] if p["id"] == "ben")
    assert ben["breakdown"]["rebuttal"] == 0.0


def test_ranked_duel_is_a_two_game_series_with_one_rating_update(world):
    fake, clock, events = world
    m = make_match(fake, ["ana", "ben"], mode="duel", size=2, ranked=True)
    run(routes.start_match(fake.table("matches").select("*").eq("id", m["id"]).execute().data[0]))
    pass_phase(clock, 60)
    speak(fake, m["id"], "ana", "opening", 90)
    speak(fake, m["id"], "ben", "opening", 80)
    pass_phase(clock, 60)
    assert status(fake, m["id"]) == "done"
    assert fake.table("ratings").select("*").execute().data == []             # no rating change after game 1
    g2 = fake.table("matches").select("*").eq("series_id", m["id"]).execute().data[0]
    side1 = {p["player_id"]: p["side"] for p in fake.table("match_players").select("*").eq("match_id", m["id"]).execute().data}
    side2 = {p["player_id"]: p["side"] for p in fake.table("match_players").select("*").eq("match_id", g2["id"]).execute().data}
    assert all(side1[p] != side2[p] for p in side1)                           # sides swapped
    assert g2["code"] != m["code"]
    pass_phase(clock, 60)
    speak(fake, g2["id"], "ana", "opening", 88)
    speak(fake, g2["id"], "ben", "opening", 84)
    pass_phase(clock, 60)
    ratings = {r["player_id"]: r for r in fake.table("ratings").select("*").execute().data}
    assert ratings["ana"]["rating"] > 1200 > ratings["ben"]["rating"] and ratings["ana"]["games"] == 1
    final = [e for e in events if e[1] == "verdict"][-1][2]
    assert final["series"]["winner"] == "ana"


def test_team_match_scores_by_team(world):
    fake, clock, _ = world
    m = make_match(fake, ["a1", "b1", "a2", "b2"], mode="team", size=4)
    run(routes.start_match(fake.table("matches").select("*").eq("id", m["id"]).execute().data[0]))
    teams = {p["player_id"]: p["team"] for p in fake.table("match_players").select("*").eq("match_id", m["id"]).execute().data}
    assert sorted(teams.values()) == ["blue", "blue", "red", "red"]
    pass_phase(clock, 60)
    for p in teams:
        speak(fake, m["id"], p, "opening", 90 if teams[p] == "red" else 75)
    pass_phase(clock, 60)
    v = fake.table("match_results").select("*").execute().data[0]["verdict"]
    assert v["mode"] == "team" and v["placements"][0]["id"] == "red"
