# Phase 4 - Post-call grading, player card, replay timeline

**Goal:** within 20 seconds of hanging up, the player sees a six-stat card and can replay the call
with every flagged moment clickable.

| | |
| --- | --- |
| Lanes | C (worker), A (card and replay UI) |
| Hours | 12 to 20 |
| Needs | Phase 2; Phase 3 fast path (step 1) |

## Done when

- [ ] Hang up on the deployed site, and the card appears in under 20 seconds
- [ ] The card shows PACE, DELIVERY, CONFIDENCE, LISTENING, FLOW and CONTENT, an overall, and one
      line per stat naming its biggest driver
- [ ] The replay plays your recording; every event marker seeks to its moment and shows its tip;
      the rapport line comes from `turns`
- [ ] Running the worker again on the same session produces a byte-identical card
- [ ] `pytest server/tests/test_card.py -q` passes

## Files

| Path | New or changed | Purpose |
| --- | --- | --- |
| `server/app/routes/sessions.py` | changed | `POST /{id}/finish`, `GET /{id}/card` |
| `server/app/workers/postcall.py` | new | Audio to card |
| `server/app/content.py` | new | Gemini labeler for CONTENT and the listener test |
| `server/app/card.py` | new | Assembles the card from measured and judged parts |
| `server/tests/test_card.py` | new | Card assembly and CONTENT math, no network |
| `web/app/play/[scenario]/page.tsx` | changed | Uploads after hanging up, then routes to the card |
| `web/app/card/[id]/page.tsx` | new | Card page |
| `web/components/PlayerCard.tsx` | new | Six stats |
| `web/components/ReplayTimeline.tsx` | new | Audio, markers, rapport line |
| `web/lib/tips.ts` | new | One tip per event type |

## Contracts

`POST /api/sessions/{id}/finish` - multipart form:

| Field | Type | Notes |
| --- | --- | --- |
| `audio` | file | `audio/webm` from `MicRecorder.stop()` |
| `persona_turns` | string | JSON `[{"start": 3.1, "end": 9.4, "text": "..."}]`, seconds on the recorder clock |

Response `202 {"status": "grading"}`. When done, broadcast `results_ready` on `session:{id}` with
`{"session_id": id}`.

`GET /api/sessions/{id}/card` - response:

```json
{
  "session_id": "uuid",
  "overall": 78.4,
  "stats": {"PACE": 88.0, "DELIVERY": 71.2, "CONFIDENCE": 80.5, "LISTENING": 82.0, "FLOW": 90.1, "CONTENT": 64.0},
  "drivers": {"LISTENING": "you followed up on 'lisbon' and remembered lisbon_trip"},
  "events": [{"t": 21.9, "type": "filler", "detail": "um", "good": false}],
  "turns": [{"idx": 3, "speaker": "user", "text": "...", "rapport_after": 12, "badge": "Good"}],
  "main_message": {"checks": {"found": true, "set_up_by_pause": true}, "score": 80},
  "content": {"labels": [], "landed": true, "notes": ["you sped up when describing your project"]},
  "audio_url": "https://...signed",
  "outcome": "won",
  "engine_version": "netmaxx_scoring@2026-10-10",
  "standards_id": null
}
```

## Steps

### 1. Upload and enqueue

In `routes/sessions.py`:

```python
import json

from fastapi import BackgroundTasks, File, Form, UploadFile

from .. import storage
from ..workers.postcall import run_postcall


@router.post("/{session_id}/finish", status_code=202)
async def finish(session_id: str, background: BackgroundTasks, audio: UploadFile = File(...),
                 persona_turns: str = Form(...), player_id: str = Depends(current_player)):
    owned(session_id, player_id)
    path = storage.put(f"sessions/{session_id}/user.webm", await audio.read())
    supabase.table("sessions").update({"audio_path": path, "persona_turns": json.loads(persona_turns),
                                       "status": "grading"}).eq("id", session_id).execute()
    background.add_task(run_postcall, session_id)
    return {"status": "grading"}


@router.get("/{session_id}/card")
def card(session_id: str, player_id: str = Depends(current_player)):
    s = owned(session_id, player_id)
    c = one("cards", session_id=session_id)
    if not c:
        raise HTTPException(409, "still grading")
    turns = supabase.table("turns").select("*").eq("session_id", session_id).order("idx").execute().data
    return {**c, "turns": turns, "audio_url": storage.signed_url(s["audio_path"]), "outcome": s["outcome"]}
```

### 2. The worker

`server/app/workers/postcall.py`:

```python
import asyncio
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from .. import grading  # noqa: F401  (sys.path)
from .. import storage
from ..card import assemble_card
from ..content import judge_content
from ..db import one, supabase
from ..realtime import broadcast
from ..scenarios import load_scenario
from grader.transcribe import transcribe          # cached by audio hash
from netmaxx_scoring import score_session

SCRIBE_CFG = {"model_id": "scribe_v2", "language_code": "en", "seed": 7}
CACHE = Path(__file__).resolve().parents[2] / "cache"


def to_wav(webm: bytes, folder: str) -> str:
    src, dst = Path(folder) / "user.webm", Path(folder) / "user.wav"
    src.write_bytes(webm)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)], check=True)
    return str(dst)


async def run_postcall(session_id: str) -> None:
    s = one("sessions", id=session_id)
    scenario = load_scenario(s["scenario_id"])
    try:
        with tempfile.TemporaryDirectory() as folder:
            wav = await asyncio.to_thread(to_wav, storage.get(s["audio_path"]), folder)
            scribe = await asyncio.to_thread(transcribe, wav, SCRIBE_CFG, str(CACHE))
            words = [w for w in scribe["words"] if w.get("type", "word") == "word"]
            measured = await asyncio.to_thread(score_session, wav, {
                "user_words": words, "agent_turns": s["persona_turns"] or [],
                "hooks": scenario.get("hooks", []), "key_sentence": s["declared_main_point"]})
        judged = await judge_content(scenario, words, s["persona_turns"] or [], s["declared_main_point"])
        card = assemble_card(measured, judged, scenario["weights"])
        supabase.table("cards").upsert({"session_id": session_id, **card}).execute()
        supabase.table("sessions").update({"status": "done"}).eq("id", session_id).execute()
        await broadcast(f"session:{session_id}", "results_ready", {"session_id": session_id})
    except Exception as exc:                                  # surface failures, never hang the UI
        supabase.table("sessions").update({"status": "failed"}).eq("id", session_id).execute()
        await broadcast(f"session:{session_id}", "results_failed", {"error": str(exc)[:200]})
        raise
```

`transcribe` is the grader's own Scribe client: it keeps fillers (`no_verbatim` false) and caches by
audio hash, which is what makes the rerun byte-identical.

### 3. CONTENT: Gemini labels, code scores

`server/app/content.py` follows the plan's "Judged metrics" section for the coffee-chat row:

```python
import asyncio
import re
from collections import Counter
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel

from .settings import settings

gemini = genai.Client(api_key=settings.gemini_api_key)
DEPTH = {"deep": 1.0, "personal": 0.7, "surface": 0.3}


class QuestionLabel(BaseModel):
    i: int
    depth: Literal["surface", "personal", "deep"]
    evidence: str


class TurnLabel(BaseModel):
    i: int
    self_disclosure: bool
    evidence: str


class ChatLabels(BaseModel):
    questions: list[QuestionLabel]
    turns: list[TurnLabel]
    main_point: str
    notes: list[str]


RUBRIC = """Label a networking conversation. You label; you do not grade.
For each numbered caller question: surface = generic or factual ("what do you do?"),
personal = about the other person's experience or opinion, deep = reasons, values or feelings.
For each numbered caller turn: self_disclosure = the caller shares something about themselves.
evidence must be words copied exactly from that numbered line.
main_point = the caller's central point in one sentence. notes = 2 or 3 plain coaching notes."""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def numbered(lines: list[str]) -> str:
    return "\n".join(f"[{i}] {line}" for i, line in enumerate(lines))


async def label_once(prompt: str) -> ChatLabels:
    r = await gemini.aio.models.generate_content(
        model=settings.judge_model, contents=prompt,
        config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                           response_schema=ChatLabels))
    return r.parsed


def content_score(questions: list[str], depths: list[str], disclosures: list[bool], landed: bool | None) -> float:
    depth_part = 100 * sum(DEPTH[d] for d in depths) / len(depths) if depths else 0.0
    disclose_part = 100 * sum(disclosures) / len(disclosures) if disclosures else 0.0
    clarity_part = 100.0 if landed else 50.0 if landed is None else 0.0
    return round(0.5 * depth_part + 0.25 * disclose_part + 0.25 * clarity_part, 1)


async def judge_content(scenario, words, persona_turns, declared_main_point) -> dict:
    user_lines = split_user_turns(words, persona_turns)          # same turn rule as the scoring engine
    questions = [l for l in user_lines if l.rstrip().endswith("?")]
    prompt = f"{RUBRIC}\n\nCaller questions:\n{numbered(questions)}\n\nCaller turns:\n{numbered(user_lines)}"
    runs = await asyncio.gather(*[label_once(prompt) for _ in range(3)])
    depths = []
    for i, q in enumerate(questions):                            # majority of 3, evidence must match
        votes = [l.depth for r in runs for l in r.questions if l.i == i and _norm(l.evidence) in _norm(q)]
        depths.append(Counter(votes).most_common(1)[0][0] if votes else "surface")
    disclosures = []
    for i, line in enumerate(user_lines):
        votes = [l.self_disclosure for r in runs for l in r.turns if l.i == i and _norm(l.evidence) in _norm(line)]
        disclosures.append(Counter(votes).most_common(1)[0][0] if votes else False)
    landed = await listener_test(runs[0].main_point, declared_main_point) if declared_main_point else None
    return {"score": content_score(questions, depths, disclosures, landed), "depths": depths,
            "disclosures": disclosures, "landed": landed, "notes": runs[0].notes}
```

The two helpers it uses, also in `content.py`, reuse the scoring engine's turn rule (1.2 s gap or a
persona turn in between) so CONTENT and the measured stats always agree on what a turn is:

```python
import numpy as np

from . import grading  # noqa: F401  (sys.path)
from netmaxx_scoring import Word, build_user_turns


def split_user_turns(words: list[dict], persona_turns: list[dict]) -> list[str]:
    ws = [Word(w["text"], float(w["start"]), float(w["end"])) for w in words]
    return [t.text for t in build_user_turns(ws, persona_turns)]


async def listener_test(model_point: str, declared: str) -> bool:
    r = await gemini.aio.models.embed_content(model="gemini-embedding-001", contents=[model_point, declared])
    a, b = (np.asarray(e.values) for e in r.embeddings)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b))) >= 0.7
```

### 4. Card assembly

`server/app/card.py`:

```python
ENGINE_VERSION = "netmaxx_scoring@2026-10-10"
STATS = ["PACE", "DELIVERY", "CONFIDENCE", "LISTENING", "FLOW", "CONTENT"]
DRIVER_EVENTS = {"LISTENING": ("follow_up", "hook_recalled", "interruption"),
                 "CONFIDENCE": ("filler", "rising_ending"),
                 "DELIVERY": ("main_message", "trailing_off"),
                 "PACE": ("long_pause",), "FLOW": ("interruption",)}


def drivers(events: list[dict]) -> dict:
    out = {}
    for stat, kinds in DRIVER_EVENTS.items():
        hits = [e["detail"] for e in events if e["type"] in kinds][:2]
        if hits:
            out[stat] = "; ".join(hits)
    return out


def assemble_card(measured: dict, judged: dict, weights: dict) -> dict:
    stats = {**measured["stats"], "CONTENT": judged["score"]}
    present = {k: v for k, v in stats.items() if v is not None}
    total_w = sum(weights[k] for k in present)
    overall = round(sum(weights[k] * v for k, v in present.items()) / total_w, 1)
    return {"overall": overall, "stats": {k: stats.get(k) for k in STATS}, "metrics": measured["metrics"],
            "events": measured["events"], "judged": judged, "engine_version": ENGINE_VERSION,
            "standards_id": measured.get("standards_id")}
```

`server/tests/test_card.py`: a measured dict with one stat `None` still yields an overall
re-weighted over the rest; `content_score` of all-deep questions, every turn disclosing and
`landed=True` is 100; `drivers` picks follow-up details for LISTENING.

### 5. Browser: upload, wait, show

After `stop()` in the call page:

```ts
const form = new FormData();
form.append("audio", blob, "user.webm");
form.append("persona_turns", JSON.stringify(turns.current));
await api(`/api/sessions/${s.session_id}/finish`, { method: "POST", body: form });
router.push(`/card/${s.session_id}`);
```

The card page subscribes to `session:{id}` for `results_ready` (and `results_failed`), polls
`GET /card` every 3 s as a fallback, then renders.

### 6. Card and replay components

`PlayerCard`: a FIFA-style card, overall in the corner, six stats with labels PAC, DEL, CON, LIS,
FLO, CNT, each with its driver line underneath. `ReplayTimeline`:

```tsx
export function ReplayTimeline({ audioUrl, events, turns, duration }: Props) {
  const audio = useRef<HTMLAudioElement>(null);
  const [tip, setTip] = useState<string>();
  const x = (t: number) => `${(100 * t) / duration}%`;
  return (
    <div>
      <audio ref={audio} src={audioUrl} controls className="w-full" />
      <div className="relative h-10 mt-2 bg-neutral-100 rounded">
        {events.map((e, i) => (
          <button key={i} title={e.detail} style={{ left: x(e.t) }}
            className={`absolute top-2 h-6 w-1.5 rounded ${e.good ? "bg-green-600" : "bg-red-600"}`}
            onClick={() => { if (audio.current) audio.current.currentTime = Math.max(0, e.t - 1.5); setTip(TIPS[e.type]); }} />
        ))}
      </div>
      {tip && <p className="mt-2 text-sm">{tip}</p>}
      {/* rapport line: plot turns.filter(t => t.rapport_after != null) as an SVG polyline over the same x axis */}
    </div>
  );
}
```

Turn rows store order, not time; place each rapport point at the start time of the matching persona
turn that follows it (`persona_turns[k].start`), which is on the same clock as the events.

`web/lib/tips.ts`:

```ts
export const TIPS: Record<string, string> = {
  filler: "Swap the um for a silent beat: a short pause reads as confidence.",
  rising_ending: "This statement rose at the end like a question. Land it flat or falling.",
  interruption: "You started while they were mid-sentence. Wait for the full stop.",
  long_pause: "A long silence mid-answer. Bridge with your next point or a short summary.",
  trailing_off: "Your volume dropped at the end. Keep the last words as strong as the first.",
  follow_up: "Great follow-up: you built on what they just said.",
  hook_recalled: "You remembered a detail they mentioned earlier. That builds rapport.",
  main_message: "Your main point: open the panel to see which of the 5 checks it passed.",
};
```

## Pitfalls

- Never call Scribe directly from the worker; always through `grader.transcribe.transcribe`, or the
  cache (and determinism) is lost.
- `BackgroundTasks` run in the same process. That is fine for one machine; if the worker is slow,
  the API still responds because the heavy calls run in `asyncio.to_thread`.
- If `results_ready` never arrives, the 3-second poll still renders the card.
