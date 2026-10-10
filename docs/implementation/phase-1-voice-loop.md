# Phase 1 - Live voice loop

**Goal:** a player talks to a Gemini persona through ElevenLabs Speech Engine in the browser, every
turn is logged, and the player's own microphone is recorded on a separate track with the persona's
speaking times on the same clock.

| | |
| --- | --- |
| Lanes | B (server), A (call screen) |
| Hours | 2 to 6 |
| Needs | Phase 0 |

## Done when

- [ ] A 2-minute conversation with the persona works on the deployed site, with headphones
- [ ] Server logs "first chunk in N ms" for every turn; the median over 10 turns is under 700 ms
- [ ] `turns` holds one row per user turn and per persona turn, in order
- [ ] After hanging up, the browser holds a playable WebM of only the player's voice and a
      `personaTurns` array where every entry has `start < end` and non-empty `text`
- [ ] The three first-hour unknowns (README) are resolved and noted at the top of `speech_engine.py`

## Files

| Path | New or changed | Purpose |
| --- | --- | --- |
| `server/scripts/create_engine.py` | new | Creates the Speech Engine resource once, prints its id |
| `server/app/scenarios.py` | new | Loads `server/scenarios/*.yaml` |
| `server/scenarios/alumni_coffee_chat.yaml` | new | The hero scenario (copy from the plan's "Modes as config") |
| `server/app/persona.py` | new | System prompt for the persona (Phase 2 adds rapport bands) |
| `server/app/speech_engine.py` | new | `on_init`, `on_transcript`, `on_close`, `on_error` |
| `server/app/routes/sessions.py` | new | Create, bind and end a session |
| `server/run.py` | changed | Starts FastAPI and the Speech Engine in one event loop |
| `web/lib/recorder.ts` | new | Mic recorder on its own track |
| `web/app/play/[scenario]/page.tsx` | new | Call screen |

## Steps

### 1. Scenario file and loader

Copy the YAML from the plan into `server/scenarios/alumni_coffee_chat.yaml`. Then
`server/app/scenarios.py`:

```python
from functools import lru_cache
from pathlib import Path

import yaml

FOLDER = Path(__file__).resolve().parents[1] / "scenarios"


@lru_cache
def load_scenario(scenario_id: str) -> dict:
    path = FOLDER / f"{scenario_id}.yaml"
    if not path.exists():
        raise KeyError(f"unknown scenario {scenario_id}")
    data = yaml.safe_load(path.read_text())
    weights = data.get("weights", {})
    if weights and abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ValueError(f"{scenario_id}: weights must sum to 1")
    return data


def seed_scenarios(supabase) -> None:
    for path in sorted(FOLDER.glob("*.yaml")):
        data = load_scenario(path.stem)
        supabase.table("scenarios").upsert({"id": data["id"], "mode": data["mode"], "config": data}).execute()


if __name__ == "__main__":
    from .db import supabase
    seed_scenarios(supabase)
    print("seeded")
```

Run `python -m app.scenarios` from `server/`.

### 2. Create the Speech Engine resource (once)

`server/scripts/create_engine.py`:

```python
import asyncio
import os

from elevenlabs import AsyncElevenLabs


async def main():
    eleven = AsyncElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    engine = await eleven.speech_engine.create(
        name="NetworkMaxx persona",
        speech_engine={"ws_url": os.environ["PUBLIC_WS_URL"]},      # wss://<host>:8443/ws
        overrides={"first_message": True},       # lets the browser set the persona's opener
    )
    print("SPEECH_ENGINE_ID =", engine.engine_id)


asyncio.run(main())
```

Put the printed id into `SPEECH_ENGINE_ID` (server `.env` and `fly secrets set`).

Checked against `elevenlabs` 2.71.0: `speech_engine.create` takes `overrides`, but
`speech_engine.update` does not, so set the first-message override at creation. If you already
created an engine without it, delete it and create a new one. `engine.serve(...)` checks the
ElevenLabs signature on every incoming connection by itself; never pass `disable_auth=True`.

### 3. Persona prompt (static for now)

`server/app/persona.py`:

```python
def persona_prompt(scenario: dict, rapport: float = 0.0, extra: str = "") -> str:
    p = scenario["persona"]
    return f"""You are {p['name']}, {p['role']}.
{p['backstory']}

You are on a voice call. Speak in 1 to 3 short sentences, like a real person on the phone.
Never mention that you are an AI, a game, scores or rapport. Never read out lists.
If the caller is vague, be politely vague back. If they are specific and curious, open up.
{extra}"""
```

Phase 2 replaces `extra` with the rapport band text and hook reveals.

### 4. Speech Engine handlers

`server/app/speech_engine.py`:

```python
"""ElevenLabs Speech Engine <-> Gemini persona.

First-hour findings (fill in): event names on the client: ___ ; conversation id read via: ___ ;
elevenlabs package version: ___
"""
import asyncio
import logging
import time

from google import genai
from google.genai import types

from .db import one, supabase
from .persona import persona_prompt
from .scenarios import load_scenario
from .settings import settings

log = logging.getLogger("speech_engine")
gemini = genai.Client(api_key=settings.gemini_api_key)


def on_init(conversation_id, session):
    log.info("engine session started %s", conversation_id)


def on_close(session):
    supabase.table("sessions").update({"status": "ended"}).eq("conversation_id", session.conversation_id) \
        .eq("status", "live").execute()


def on_error(err, session):
    log.error("engine error %s", err)


def _contents(transcript):
    return [types.Content(role="model" if m.role == "agent" else "user", parts=[types.Part(text=m.content)])
            for m in transcript]


async def on_transcript(transcript, session):
    game = one("sessions", conversation_id=session.conversation_id)
    scenario = load_scenario(game["scenario_id"]) if game else None
    system = persona_prompt(scenario, game["rapport"]) if game else "Reply briefly and kindly."

    t_in = time.perf_counter()
    stream = await gemini.aio.models.generate_content_stream(
        model=settings.persona_model,
        contents=_contents(transcript),
        config=types.GenerateContentConfig(
            system_instruction=system, temperature=0.8, max_output_tokens=160,
            thinking_config=types.ThinkingConfig(thinking_level="low"),   # if rejected: thinking_budget=0
        ),
    )
    spoken, first = [], None

    async def chunks():
        nonlocal first
        async for chunk in stream:
            if chunk.text:
                if first is None:
                    first = (time.perf_counter() - t_in) * 1000
                    log.info("first chunk in %.0f ms", first)
                spoken.append(chunk.text)
                yield chunk.text

    interrupted = False
    try:
        await session.send_response(chunks())
    except asyncio.CancelledError:              # the player spoke over the persona
        interrupted = True
        raise
    finally:
        if game:
            n = len(transcript)
            supabase.table("turns").upsert([
                {"session_id": game["id"], "idx": n - 1, "speaker": "user", "text": transcript[-1].content},
                {"session_id": game["id"], "idx": n, "speaker": "agent", "text": "".join(spoken),
                 "reason": "interrupted" if interrupted else None},
            ]).execute()
```

### 5. Session routes

`server/app/routes/sessions.py`:

```python
from elevenlabs import AsyncElevenLabs
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_player
from ..db import one, supabase
from ..scenarios import load_scenario
from ..settings import settings

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
eleven = AsyncElevenLabs(api_key=settings.elevenlabs_api_key)


class CreateSession(BaseModel):
    scenario_id: str
    declared_main_point: str | None = None


class Bind(BaseModel):
    conversation_id: str


def owned(session_id: str, player_id: str) -> dict:
    row = one("sessions", id=session_id)
    if not row or row["user_id"] != player_id:
        raise HTTPException(404, "no such session")
    return row


@router.post("")
async def create_session(body: CreateSession, player_id: str = Depends(current_player)):
    scenario = load_scenario(body.scenario_id)
    row = supabase.table("sessions").insert({
        "user_id": player_id, "scenario_id": body.scenario_id,
        "rapport": scenario["rapport"]["initial"], "declared_main_point": body.declared_main_point,
    }).execute().data[0]
    token = await eleven.conversational_ai.conversations.get_webrtc_token(agent_id=settings.speech_engine_id)
    return {"session_id": row["id"], "token": token.token, "first_message": scenario["persona"]["opener"],
            "time_limit_s": scenario["time_limit_s"], "rapport": row["rapport"]}


@router.post("/{session_id}/bind")
def bind(session_id: str, body: Bind, player_id: str = Depends(current_player)):
    owned(session_id, player_id)
    supabase.table("sessions").update({"conversation_id": body.conversation_id, "status": "live"}) \
        .eq("id", session_id).execute()
    return {"ok": True}


@router.post("/{session_id}/end")
def end(session_id: str, player_id: str = Depends(current_player)):
    owned(session_id, player_id)
    supabase.table("sessions").update({"status": "ended", "ended_at": "now()"}).eq("id", session_id).execute()
    return {"ok": True}
```

Register it in `main.py`: `from .routes import sessions` and `app.include_router(sessions.router)`.

### 6. Run both servers in one process

`server/run.py`:

```python
import asyncio

import uvicorn
from elevenlabs import AsyncElevenLabs

from app import speech_engine
from app.main import app
from app.settings import settings


async def main():
    eleven = AsyncElevenLabs(api_key=settings.elevenlabs_api_key)
    engine = await eleven.speech_engine.get(settings.speech_engine_id)
    api = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info"))
    await asyncio.gather(
        api.serve(),
        engine.serve(port=3001, path="/ws", debug=False,
                     on_init=speech_engine.on_init, on_transcript=speech_engine.on_transcript,
                     on_close=speech_engine.on_close, on_error=speech_engine.on_error),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

### 7. Browser recorder

`web/lib/recorder.ts`:

```ts
export class MicRecorder {
  private rec?: MediaRecorder;
  private chunks: Blob[] = [];
  private t0 = 0;

  async start() {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, autoGainControl: false, noiseSuppression: false },
    });
    this.rec = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
    this.chunks = [];
    this.rec.ondataavailable = (e) => { if (e.data.size) this.chunks.push(e.data); };
    this.rec.start(1000);
    this.t0 = performance.now();
  }

  /** Seconds since recording started: the clock persona turns are logged on. */
  now() { return (performance.now() - this.t0) / 1000; }

  stop(): Promise<Blob> {
    return new Promise((resolve) => {
      const rec = this.rec!;
      rec.onstop = () => {
        rec.stream.getTracks().forEach((t) => t.stop());
        resolve(new Blob(this.chunks, { type: "audio/webm" }));
      };
      rec.stop();
    });
  }
}
```

### 8. Call screen

`web/app/play/[scenario]/page.tsx` (core of it; style later):

```tsx
"use client";
import { useConversation } from "@elevenlabs/react";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { MicRecorder } from "@/lib/recorder";

type PersonaTurn = { start: number; end: number; text: string };

export default function Play({ params }: { params: { scenario: string } }) {
  const recorder = useRef(new MicRecorder());
  const turns = useRef<PersonaTurn[]>([]);
  const [session, setSession] = useState<{ session_id: string } | null>(null);
  const [blobUrl, setBlobUrl] = useState<string>();

  const conversation = useConversation({
    // Confirm these event shapes in the @elevenlabs/react reference (first-hour item 1).
    onModeChange: ({ mode }: { mode: string }) => {
      const t = recorder.current.now();
      if (mode === "speaking") turns.current.push({ start: t, end: t, text: "" });
      else if (turns.current.length) turns.current.at(-1)!.end = t;
    },
    onMessage: ({ message, source }: { message: string; source: string }) => {
      if (source === "ai" && turns.current.length) turns.current.at(-1)!.text += message;
    },
  });

  async function start() {
    await recorder.current.start();                    // asks for the mic, starts the clock
    const s = await api<any>("/api/sessions", { method: "POST", body: JSON.stringify({ scenario_id: params.scenario }) });
    setSession(s);
    const conversationId = await conversation.startSession({
      conversationToken: s.token,
      overrides: { agent: { firstMessage: s.first_message } },
    });
    await api(`/api/sessions/${s.session_id}/bind`, { method: "POST", body: JSON.stringify({ conversation_id: conversationId }) });
  }

  async function stop() {
    await conversation.endSession();
    const blob = await recorder.current.stop();
    setBlobUrl(URL.createObjectURL(blob));
    console.log("persona turns", turns.current);       // Phase 4 uploads both
    if (session) await api(`/api/sessions/${session.session_id}/end`, { method: "POST" });
  }

  return (
    <main className="p-6 space-y-4">
      <p>Status: {conversation.status} - wear headphones</p>
      <button onClick={start} disabled={conversation.status === "connected"}>Start</button>
      <button onClick={stop} disabled={conversation.status !== "connected"}>Hang up</button>
      {blobUrl && <audio controls src={blobUrl} />}
    </main>
  );
}
```

If `startSession` does not return the id in your SDK version, read it with `conversation.getId()`
right after it resolves.

## Pitfalls

- **Speakers instead of headphones** put the persona's voice into the mic track and break every
  timing metric. Show a "headphones on?" check before Start.
- **First user turn before `/bind`:** `on_transcript` falls back to a neutral reply if it cannot find
  the session yet. If it happens often, call `/bind` before speaking is possible (keep the mic muted
  until bind returns).
- **Latency:** if the median first chunk is over 700 ms, drop `max_output_tokens` to 120, confirm
  thinking is set low, and keep the server in the region closest to ElevenLabs.
