import asyncio

from app import rapport
from app.rapport import badge, nudges, state_for

SCENARIO = {
    "persona": {"name": "Dana", "role": "PM", "backstory": "busy", "opener": "Back from Lisbon."},
    "hidden_goal": {"ask_detector": "a referral"},
    "rapport": {"initial": -10, "win_threshold": 60, "lose_threshold": -80},
    "ask_keywords": ["referral", "refer me"],
    "hooks": [{"id": "lisbon_trip", "keywords": ["lisbon"], "planted_in": "opener", "line": "x"}],
}


def test_badges():
    assert [badge(d) for d in (10, 6, 2, 0, -2, -5, -9)] == \
        ["Brilliant!!", "Great!", "Good", None, "Inaccuracy?!", "Mistake?", "Blunder??"]


def test_follow_up_and_hook():
    s = state_for("t1", SCENARIO)
    total, why = nudges(SCENARIO, s, 0, "I just got back from Lisbon.", "How was Lisbon?", False)
    assert total == 9 and "remembered lisbon_trip +5" in why


def test_premature_ask():
    s = state_for("t2", SCENARIO)
    total, _ = nudges(SCENARIO, s, 10, "Nice to meet you.", "Could you give me a referral", True)
    assert total == -8


def test_update_wins(monkeypatch):
    async def fake_judge(*a):
        return rapport.RapportJudgment(delta=5, reason="warm", ask_made=True)
    sent = []
    async def fake_broadcast(topic, event, payload):
        sent.append(event)
    monkeypatch.setattr(rapport, "judge", fake_judge)
    monkeypatch.setattr(rapport, "broadcast", fake_broadcast)
    monkeypatch.setattr(rapport, "supabase", _Null())
    # 62 is already past the 60 threshold, so the ask is not premature: 62 + 5 = 67, and it wins.
    out = asyncio.run(rapport.update({"id": "t3", "rapport": 62}, SCENARIO, "hi", "Would you refer me?", 3))
    assert out["rapport"] == 67 and "ended" in sent


def test_same_ask_too_early_is_penalized(monkeypatch):
    async def fake_judge(*a):
        return rapport.RapportJudgment(delta=5, reason="warm", ask_made=True)
    async def fake_broadcast(*a):
        return None
    monkeypatch.setattr(rapport, "judge", fake_judge)
    monkeypatch.setattr(rapport, "broadcast", fake_broadcast)
    monkeypatch.setattr(rapport, "supabase", _Null())
    out = asyncio.run(rapport.update({"id": "t4", "rapport": 58}, SCENARIO, "hi", "Would you refer me?", 3))
    assert out["rapport"] == 55 and out["badge"] == "Inaccuracy?!"      # 5 - 8 = -3


class _Null:
    def __getattr__(self, _):
        return lambda *a, **k: self
