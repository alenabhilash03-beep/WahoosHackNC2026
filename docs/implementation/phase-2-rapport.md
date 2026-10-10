# Phase 2 - Rapport meter, scenarios, win and lose

**Goal:** every user turn moves a rapport number from -100 to +100, the browser shows it as a chess
eval bar with a move badge, the persona's behavior follows the number, and a round ends in a win,
a loss or a timeout.

| | |
| --- | --- |
| Lanes | B (server), A (eval bar) |
| Hours | 6 to 12 |
| Needs | Phase 1 |

## Done when

- [ ] `pytest server/tests/test_rapport.py -q` passes (no network: Gemini is stubbed)
- [ ] Scripted run on the deployed site: follow up on Lisbon, the bar rises and the badge is Good or
      better; ask for a referral in the first 30 seconds, the badge is Blunder?? and Dana deflects
- [ ] Making the ask after rapport passes 60 ends the round as **won**; talking for 4 minutes ends
      it as **timeout**
- [ ] Each `turns` row for the user has `rapport_after`, `delta`, `badge` and `reason`
- [ ] 20 calls of the rapport judge in a row all return valid JSON

## Files

| Path | New or changed | Purpose |
| --- | --- | --- |
| `server/scenarios/alumni_coffee_chat.yaml` | changed | Add `ask_keywords` and a `line` per hook |
| `server/app/persona.py` | changed | Band text, hook reveal, ask handling |
| `server/app/rapport.py` | new | Judge, nudges, badges, state, win and lose |
| `server/app/speech_engine.py` | changed | Calls rapport each turn; interruption flag |
| `server/tests/test_rapport.py` | new | Unit tests |
| `web/components/EvalBar.tsx` | new | The bar |
| `web/components/MoveBadge.tsx` | new | The badge |
| `web/app/play/[scenario]/page.tsx` | changed | Subscribes to `session:{id}` |

## Steps

### 1. Scenario additions

Add to the scenario YAML:

```yaml
ask_keywords: [referral, refer me, introduce me, intro, put in a word, recommend me, pass my resume]
hooks:
  - id: lisbon_trip
    keywords: [lisbon, portugal, trip]
    planted_in: opener
    line: "I just got back from Lisbon on Sunday."
  - id: team_hiring
    keywords: [hiring, headcount, team, growing]
    planted_in: warm_band
    line: "My team is hiring two associate PMs next quarter, actually."
```

### 2. Persona bands

Replace `server/app/persona.py`:

```python
BANDS = [
    (-100, -40, "You are cold: one-sentence answers, no personal details, you hint that you need to go."),
    (-40, 20, "You are neutral and polite: you answer what is asked and rarely ask anything back."),
    (20, 60, "You are warming up: you tell short stories and ask the caller questions back."),
    (60, 101, "You are won over: you offer help without being asked."),
]


def band_text(rapport: float) -> str:
    return next(text for lo, hi, text in BANDS if lo <= rapport < hi)


def persona_prompt(scenario: dict, rapport: float, state: dict | None = None) -> str:
    state = state or {}
    p = scenario["persona"]
    lines = [band_text(rapport)]
    for hook in scenario.get("hooks", []):
        if hook["planted_in"] == "warm_band" and rapport >= 20 and hook["id"] not in state.get("planted", set()):
            lines.append(f'Work this into your next reply naturally: "{hook["line"]}"')
    if state.get("ask_now") == "grant":
        lines.append("The caller just asked for help. Say yes warmly and say what you will do.")
    elif state.get("ask_now") == "deflect":
        lines.append("The caller just asked for a favor too early. Deflect politely; you barely know them.")
    if state.get("wrap_up"):
        lines.append("End the call politely now in one sentence.")
    return f"""You are {p['name']}, {p['role']}.
{p['backstory']}

You are on a voice call. Speak in 1 to 3 short sentences. Never mention AI, games, scores or rapport.
{chr(10).join(lines)}"""
```

### 3. Rapport module

`server/app/rapport.py`:

```python
"""Rapport: Gemini judges the exchange, deterministic nudges adjust it, code decides the badge."""
import asyncio

from google import genai
from google.genai import types
from pydantic import BaseModel

from . import grading  # noqa: F401  (puts conversation/ on sys.path)
from netmaxx_scoring import HEDGE_PHRASES, REFERENTIAL, content_stems, norm, phrase_count, stem
from .db import supabase
from .realtime import broadcast
from .settings import settings

gemini = genai.Client(api_key=settings.gemini_api_key)
STATE: dict[str, dict] = {}        # per session id; one server machine (fly scale count 1)

BADGES = [(9, "Brilliant!!"), (5, "Great!"), (1, "Good"), (0, None),
          (-3, "Inaccuracy?!"), (-7, "Mistake?"), (-100, "Blunder??")]


class RapportJudgment(BaseModel):
    delta: int
    reason: str
    ask_made: bool


def badge(delta: int):
    return next(label for floor, label in BADGES if delta >= floor)


def state_for(session_id: str, scenario: dict) -> dict:
    if session_id not in STATE:
        planted = {h["id"] for h in scenario.get("hooks", []) if h["planted_in"] == "opener"}
        STATE[session_id] = {"planted": planted, "recalled": set(), "interrupted_last": False,
                             "ask_now": None, "wrap_up": False, "outcome": None}
    return STATE[session_id]


def ask_detected(scenario: dict, user_text: str) -> bool:
    text = " " + " ".join(norm(t) for t in user_text.split()) + " "
    return any(f" {k} " in text for k in scenario.get("ask_keywords", []))


def is_follow_up(agent_text: str, user_text: str) -> bool:
    asks = user_text.strip().endswith("?") or user_text.split()[:1] and norm(user_text.split()[0]) in {
        "who", "what", "when", "where", "why", "how", "which", "do", "did", "are", "is", "was", "have", "can"}
    if not asks:
        return False
    shared = content_stems(user_text) & content_stems(agent_text)
    return bool(shared) or any(norm(w) in REFERENTIAL for w in user_text.split())


def nudges(scenario: dict, state: dict, rapport: float, agent_text: str, user_text: str, ask_made: bool):
    total, why = 0, []
    if is_follow_up(agent_text, user_text):
        total += 4; why.append("follow-up question +4")
    said = content_stems(user_text)
    for hook in scenario.get("hooks", []):
        keys = {stem(norm(k)) for k in hook["keywords"]}
        if hook["id"] in state["planted"] and hook["id"] not in state["recalled"] and said & keys:
            state["recalled"].add(hook["id"]); total += 5; why.append(f"remembered {hook['id']} +5")
    if state["interrupted_last"]:
        total -= 5; why.append("cut them off -5"); state["interrupted_last"] = False
    if len(user_text.split()) > 90:
        total -= 3; why.append("monologue -3")
    if phrase_count(user_text, HEDGE_PHRASES) >= 2:
        total -= 1; why.append("hedging -1")
    if ask_made and rapport < scenario["rapport"]["win_threshold"]:
        total -= 8; why.append("asked too early -8")
    return total, why


async def judge(scenario: dict, rapport: float, agent_text: str, user_text: str) -> RapportJudgment:
    p, goal = scenario["persona"], scenario["hidden_goal"]
    prompt = f"""You are scoring one moment of a networking call from {p['name']}'s point of view.
{p['name']}: {p['role']}. {p['backstory']}
Current warmth toward the caller: {rapport:.0f} on a -100 to 100 scale.
{p['name']} just said: "{agent_text}"
The caller replied: "{user_text}"

Return JSON: delta = how much warmer (positive) or colder (negative) {p['name']} feels after this
reply, an integer from -10 to 10; reason = 12 words or fewer; ask_made = true only if the caller
explicitly asked for this: {goal['ask_detector']}."""
    r = await gemini.aio.models.generate_content(
        model=settings.judge_model, contents=prompt,
        config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                           response_schema=RapportJudgment))
    return r.parsed


async def update(game: dict, scenario: dict, agent_text: str, user_text: str, idx: int) -> dict:
    state = state_for(game["id"], scenario)
    rapport = float(game["rapport"])
    j = await judge(scenario, rapport, agent_text, user_text)
    bonus, why = nudges(scenario, state, rapport, agent_text, user_text, j.ask_made)
    delta = max(-10, min(10, j.delta)) + bonus
    new = max(-100, min(100, rapport + delta))
    for hook in scenario.get("hooks", []):                 # warm-band hooks count as planted once revealed
        if hook["planted_in"] == "warm_band" and new >= 20:
            state["planted"].add(hook["id"])
    if j.ask_made and new >= scenario["rapport"]["win_threshold"]:
        state["outcome"] = "won"
    elif new <= scenario["rapport"]["lose_threshold"]:
        state["outcome"], state["wrap_up"] = "lost", True
    row = {"rapport_after": new, "delta": delta, "badge": badge(delta), "reason": j.reason}
    supabase.table("turns").update(row).eq("session_id", game["id"]).eq("idx", idx).execute()
    supabase.table("sessions").update({"rapport": new, "outcome": state["outcome"]}).eq("id", game["id"]).execute()
    payload = {"idx": idx, "rapport": new, **row, "nudges": why}
    await broadcast(f"session:{game['id']}", "rapport", payload)
    if state["outcome"]:
        await broadcast(f"session:{game['id']}", "ended", {"outcome": state["outcome"]})
    return payload
```

### 4. Wire it into the Speech Engine handler

In `on_transcript`, before calling Gemini for the persona reply:

```python
    state = rapport.state_for(game["id"], scenario)
    user_text = transcript[-1].content
    agent_text = transcript[-2].content if len(transcript) > 1 else scenario["persona"]["opener"]
    state["ask_now"] = None
    if rapport.ask_detected(scenario, user_text):          # fast keyword check, so THIS reply reacts
        state["ask_now"] = "grant" if game["rapport"] >= scenario["rapport"]["win_threshold"] else "deflect"
    system = persona_prompt(scenario, game["rapport"], state)
    asyncio.create_task(rapport.update(game, scenario, agent_text, user_text, idx=len(transcript) - 1))
```

and in the `except asyncio.CancelledError` branch set `rapport.state_for(game["id"], scenario)["interrupted_last"] = True`.
The user turn row must exist before `update` writes to it, so insert the user row at the start of
`on_transcript` and the persona row in `finally`.

### 5. Tests

Both files below are also in `docs/implementation/tests/`, ready to copy into `server/tests/`. They
were run against this phase's code assembled exactly as written here: 5 of 5 rapport tests pass.

`server/tests/conftest.py` (lets the app modules import without real keys):

```python
import os
import sys
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test.test.test")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("ELEVENLABS_API_KEY", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

`server/tests/test_rapport.py`:

```python
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
```

### 6. Eval bar and badge

`web/components/EvalBar.tsx`:

```tsx
export function EvalBar({ rapport }: { rapport: number }) {
  const pct = (rapport + 100) / 2;                       // -100..100 -> 0..100
  const label = (rapport >= 0 ? "+" : "") + (rapport / 10).toFixed(1);
  return (
    <div className="relative h-80 w-8 rounded bg-neutral-800 overflow-hidden" aria-label={`rapport ${label}`}>
      <div className="absolute bottom-0 w-full bg-white transition-all duration-700" style={{ height: `${pct}%` }} />
      <span className="absolute top-1 w-full text-center text-xs mix-blend-difference text-white">{label}</span>
    </div>
  );
}
```

`web/components/MoveBadge.tsx`: shows the latest badge for 2.5 s next to the bar; colors:
Brilliant teal, Great blue, Good green, Inaccuracy yellow, Mistake orange, Blunder red. Keep a
list of all badges under the bar for the replay screen.

On the call page, after `/bind` succeeds:

```tsx
supabase.channel(`session:${s.session_id}`)
  .on("broadcast", { event: "rapport" }, ({ payload }) => { setRapport(payload.rapport); showBadge(payload.badge); })
  .on("broadcast", { event: "ended" }, ({ payload }) => setOutcome(payload.outcome))
  .subscribe();
```

When `outcome` is set, wait for the persona's current sentence to finish (next mode change to
`listening`), then run `stop()`. Add a visible 4-minute countdown that calls `stop()` at zero; the
server marks `timeout` in `/end` when `outcome` is still empty.

## Pitfalls

- `STATE` lives in memory: run exactly one server machine (`fly scale count 1`) and never auto-scale.
- Gemini's response schema does not enforce number ranges: always clamp `delta` in code.
- The keyword ask detector decides the persona's reaction this turn; the judge's `ask_made` decides
  scoring. If they disagree often, add the missed phrasings to `ask_keywords`.
