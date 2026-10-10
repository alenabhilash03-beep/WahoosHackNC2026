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
      persona turns array where every entry has `start < end` and non-empty `text`
- [ ] `python tools/checks/check_engine.py` passes, and the voice check summary (step 9) is pasted
      at the top of `speech_engine.py`

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
| `web/lib/personaTimeline.ts` | new | Persona speaking times and text, on the recorder clock |
| `web/app/play/[scenario]/page.tsx` | new | Call screen |
| `web/app/dev/voice-check/page.tsx` | new | Copy of `tools/checks/voice-check/page.tsx`: logs every SDK event |

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

# The events ElevenLabs forwards to the browser. Each one feeds a call-screen callback:
# agent_response / user_transcript -> onMessage, interruption -> onInterruption,
# agent_response_correction -> onAgentResponseCorrection. A missing one fails silently.
CLIENT_EVENTS = [
    "conversation_initiation_metadata", "ping", "audio", "interruption",
    "user_transcript", "agent_response", "agent_response_correction",
]


async def main():
    eleven = AsyncElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    engine = await eleven.speech_engine.create(
        name="NetworkMaxx persona",
        speech_engine={"ws_url": os.environ["PUBLIC_WS_URL"]},      # wss://<host>:8443/ws
        conversation={"client_events": CLIENT_EVENTS},
        overrides={"first_message": True},       # lets the browser set the persona's opener
    )
    print("SPEECH_ENGINE_ID =", engine.engine_id)


asyncio.run(main())
```

Put the printed id into `SPEECH_ENGINE_ID` (server `.env` and `fly secrets set`), then run
`python tools/checks/check_engine.py`. It must print PASS for client events, the first-message
override and `ws_url`. If the engine was created earlier without the client events,
`check_engine.py --fix` adds them in place.

Checked against `elevenlabs` 2.71.0: `speech_engine.create` takes `overrides`, but
`speech_engine.update` does not, so set the first-message override at creation. If you already
created an engine without it, delete it and create a new one. `update` does accept `conversation`
and `speech_engine`, so client events and the WebSocket URL can be changed later.
`engine.serve(...)` checks the ElevenLabs signature on every incoming connection by itself; never
pass `disable_auth=True`.

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

Voice check summary (paste from /dev/voice-check, step 9):
___
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

What the browser SDK gives you, read from the published code of `@elevenlabs/react` 1.17.0 and
`@elevenlabs/client` 1.27.0 (step 9 confirms it on your account):

| Callback | Payload | Used for |
| --- | --- | --- |
| `onConnect` | `{ conversationId }` | `/bind`. `startSession()` itself returns nothing |
| `onAudio` | base64 audio, one call per 25 ms of audible persona audio | when the persona was audible |
| `onMessage` | `{ message, role: "agent" \| "user", event_id, response_id? }` | persona text; a `user` message closes the persona's turn |
| `onInterruption` | `{ event_id }` | marks the turn the player cut into |
| `onAgentResponseCorrection` | `{ original_agent_response, corrected_agent_response, event_id }` | what was actually said before the cut |
| `onModeChange` | `{ mode: "speaking" \| "listening" }` | UI only (see below) |
| `status` (returned by the hook, not a callback) | `"disconnected" \| "connecting" \| "connected" \| "error"` | buttons and error state |

Three rules that are easy to get wrong:

- Every conversation hook must be rendered inside `<ConversationProvider>`; outside it,
  `useConversation` throws.
- `startSession(...)` returns `undefined`. Read the conversation id in `onConnect` (or
  `conversation.getId()` after it). `onConnect` fires before the opener finishes, so `/bind` lands
  before the player's first turn reaches `on_transcript`.
- A `conversationToken` session runs over WebRTC, where `onModeChange("listening")` follows
  LiveKit's server-side active-speaker updates and can lag the real end of the persona's audio.
  The latency and interruption stats need the real end, so persona turns are timed from `onAudio`
  instead. `source: "ai"` still works in `onMessage` but is deprecated; use `role`.

`web/lib/personaTimeline.ts`:

```ts
/**
 * When the persona was audible, on the recorder clock, and what it said in each turn.
 *
 * Built from @elevenlabs/react callbacks (checked against @elevenlabs/client 1.27):
 * - onAudio fires once per 25 ms chunk of persona audio louder than 0.01, measured in the browser
 *   on the incoming WebRTC track. That is the timing source: a span ends within about 25 ms of the
 *   persona going quiet.
 * - onModeChange("listening") comes from LiveKit's server-side active-speaker updates and can arrive
 *   noticeably later, so it only drives the UI (the voice check page measures the gap).
 * - onMessage { role: "agent" } carries the persona's text; it can arrive just before or just after
 *   that turn's audio starts. { role: "user" } is the player's final transcript and closes the turn.
 * - onInterruption marks the turn the player cut into; onAgentResponseCorrection replaces its text
 *   with what was actually spoken before the cut.
 */
export type PersonaTurn = { start: number; end: number; text: string; interrupted: boolean };

const CHUNK_S = 0.025;     // one onAudio call covers the 25 ms before it
const NEW_TURN_GAP_S = 1.5; // silence that starts a new turn even without a player transcript
const TEXT_GRACE_S = 0.5;   // agent text this soon after audio belongs to the turn still playing

type Turn = PersonaTurn & { responseIds: string[] };

export class PersonaTimeline {
  private readonly list: Turn[] = [];
  private open = false;
  private pending: { text: string; responseId?: string }[] = [];

  constructor(private readonly now: () => number) {}

  /** onAudio */
  audio(): void {
    const t = this.now();
    const cur = this.list.at(-1);
    if (this.open && cur && t - cur.end <= NEW_TURN_GAP_S) {
      cur.end = Math.max(cur.end, t);
    } else {
      this.list.push({ start: Math.max(0, t - CHUNK_S), end: t, text: "", interrupted: false, responseIds: [] });
      this.open = true;
    }
    this.flushPending();
  }

  /** onMessage with role "agent" */
  agentText(text: string, responseId?: string): void {
    const cur = this.list.at(-1);
    const sameResponse = responseId !== undefined && cur?.responseIds.includes(responseId);
    const waitingForText = cur?.text === "";       // audio started first; the player has not spoken since
    if (cur && this.open && (waitingForText || sameResponse || this.now() - cur.end <= TEXT_GRACE_S)) {
      append(cur, text, responseId);
    } else {
      this.pending.push({ text, responseId });
    }
  }

  /** onMessage with role "user": the player has taken the floor. */
  userFinal(): void {
    this.open = false;
  }

  /** onInterruption */
  interrupted(): void {
    const cur = this.list.at(-1);
    if (cur && this.open) cur.interrupted = true;
  }

  /** onAgentResponseCorrection: keep only what the persona actually said. */
  correct(original: string, corrected: string, responseId?: string): void {
    const byId = responseId ? [...this.list].reverse().find((t) => t.responseIds.includes(responseId)) : undefined;
    const turn = byId ?? [...this.list].reverse().find((t) => t.text.includes(original));
    if (turn) {
      turn.text = turn.text.includes(original) ? turn.text.replace(original, corrected) : corrected;
      turn.interrupted = true;
      return;
    }
    const queued = this.pending.find((p) => p.text === original || (responseId && p.responseId === responseId));
    if (queued) queued.text = corrected;
  }

  /** The persona turns to upload (Phase 4 `persona_turns`). */
  get turns(): PersonaTurn[] {
    return this.list.map(({ start, end, text, interrupted }) => ({
      start: round(start), end: round(end), text, interrupted,
    }));
  }

  private flushPending(): void {
    const cur = this.list.at(-1);
    if (!cur || !this.pending.length) return;
    for (const p of this.pending) append(cur, p.text, p.responseId);
    this.pending = [];
  }
}

function append(turn: Turn, text: string, responseId?: string) {
  turn.text = turn.text ? `${turn.text} ${text}` : text;
  if (responseId && !turn.responseIds.includes(responseId)) turn.responseIds.push(responseId);
}

const round = (x: number) => Math.round(x * 1000) / 1000;
```

`web/app/play/[scenario]/page.tsx` (core of it; style later):

```tsx
"use client";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { PersonaTimeline } from "@/lib/personaTimeline";
import { MicRecorder } from "@/lib/recorder";

type CreatedSession = { session_id: string; token: string; first_message: string; time_limit_s: number; rapport: number };

export default function PlayPage() {
  // @elevenlabs/react 1.x: every conversation hook must be inside a ConversationProvider.
  return (
    <ConversationProvider>
      <Play />
    </ConversationProvider>
  );
}

function Play() {
  const { scenario } = useParams<{ scenario: string }>();   // Next 15+: page params are no longer a plain prop
  const recorder = useRef(new MicRecorder());
  const timeline = useRef(new PersonaTimeline(() => recorder.current.now()));
  const session = useRef<CreatedSession | null>(null);
  const [blobUrl, setBlobUrl] = useState<string>();
  const [error, setError] = useState<string>();

  const conversation = useConversation({
    // startSession() returns nothing; the conversation id arrives here, before the player can speak.
    onConnect: ({ conversationId }) => {
      const s = session.current;
      if (!s) return;
      api(`/api/sessions/${s.session_id}/bind`, { method: "POST", body: JSON.stringify({ conversation_id: conversationId }) })
        .catch((e) => setError(String(e)));
    },
    onAudio: () => timeline.current.audio(),                  // every 25 ms the persona is audible
    onMessage: ({ role, message, response_id }) => {
      if (role === "agent") timeline.current.agentText(message, response_id);
      else timeline.current.userFinal();                      // the player's final transcript
    },
    onInterruption: () => timeline.current.interrupted(),
    onAgentResponseCorrection: ({ original_agent_response, corrected_agent_response, response_id }) =>
      timeline.current.correct(original_agent_response, corrected_agent_response, response_id),
    onError: (message) => setError(message),
  });

  async function start() {
    setError(undefined);
    timeline.current = new PersonaTimeline(() => recorder.current.now());
    await recorder.current.start();                           // asks for the mic, starts the clock
    const s = await api<CreatedSession>("/api/sessions", { method: "POST", body: JSON.stringify({ scenario_id: scenario }) });
    session.current = s;
    conversation.startSession({ conversationToken: s.token, overrides: { agent: { firstMessage: s.first_message } } });
  }

  async function stop() {
    conversation.endSession();
    const blob = await recorder.current.stop();
    setBlobUrl(URL.createObjectURL(blob));
    console.log("persona turns", timeline.current.turns);    // Phase 4 uploads both
    if (session.current) await api(`/api/sessions/${session.current.session_id}/end`, { method: "POST" });
  }

  return (
    <main className="p-6 space-y-4">
      <p>Status: {conversation.status} - wear headphones</p>
      {error && <p className="text-red-600">{error}</p>}
      <button onClick={start} disabled={conversation.status === "connected" || conversation.status === "connecting"}>Start</button>
      <button onClick={stop} disabled={conversation.status !== "connected"}>Hang up</button>
      {blobUrl && <audio controls src={blobUrl} />}
    </main>
  );
}
```

`useParams` replaces the `params` prop: in Next.js 15 and later, page `params` is a promise.

Tests for the timeline (text arriving before or after its audio, pauses inside one answer,
interruptions and corrections) are in `docs/implementation/tests/web/personaTimeline.test.ts`:
`npm i -D vitest`, copy it to `web/test/`, run `npx vitest run`.

### 9. Voice check (15 min)

Copy `tools/checks/voice-check/page.tsx` to `web/app/dev/voice-check/page.tsx`, open
`/dev/voice-check` on the deployed site (or localhost with both servers running), press Start,
talk for about a minute with headphones on, cut the persona off once mid-sentence, then press
Stop and Copy summary. It looks like this (numbers made up):

```text
conversation id: onConnect=conv_01k... getId()=conv_01k...
startSession() return value: {"value":"undefined"}
incoming event types: agent_response x5, audio x2405, user_transcript x4, interruption x1, agent_response_correction x1, ping x9
payload keys onMessage: message, role, source, event_id
onAudio: 2405 chunks in 21 bursts; 610 chunks after the first interruption
mode->listening lag after audio stops: median 0.38 s (all: ...)
agent text vs its audio start (negative = text first): -0.41, -0.36, ...
```

Check each line:

| Line | Expect | If not |
| --- | --- | --- |
| conversation id | both the same, and equal to the server's `engine session started <id>` log line | `on_transcript` cannot find the session and the persona stays neutral: make `/bind` store the form the server logs (for example, strip a prefix) |
| incoming event types | `agent_response`, `user_transcript`, `interruption` and `agent_response_correction` present. `audio` is made in the browser on WebRTC, so it proves nothing about the server | `python tools/checks/check_engine.py --fix`, then retry |
| onAudio | thousands of chunks, still counting after the interruption | time turns from mode changes instead (block below) |
| mode lag | any value; it is how much later the timing would be with `onModeChange` | nothing to do |
| agent text order | negative or positive; `PersonaTimeline` handles both | nothing to do |

Fallback if `onAudio` stops firing: drop `onAudio` from the call screen and drive the same timeline
from mode changes (the turn ends up to the measured lag late):

```tsx
const tick = useRef<number | undefined>(undefined);
// inside useConversation({...}):
onModeChange: ({ mode }) => {
  window.clearInterval(tick.current);
  if (mode === "speaking") tick.current = window.setInterval(() => timeline.current.audio(), 25);
},
```

Paste the summary into the docstring at the top of `server/app/speech_engine.py`.

## Pitfalls

- **Speakers instead of headphones** put the persona's voice into the mic track and break every
  timing metric. Show a "headphones on?" check before Start.
- **First user turn before `/bind`:** `on_transcript` falls back to a neutral reply if it cannot find
  the session yet. `/bind` runs in `onConnect`, which fires before the opener has finished playing,
  so this should be rare. If it happens, start the session muted (`useConversation({ micMuted })`
  with state that flips to `false` once `/bind` returns).
- **`startSession` errors do not throw.** It returns nothing and reports failures through
  `onError` and `status === "error"`, so show `error` on screen as the page above does.
- **Latency:** if the median first chunk is over 700 ms, drop `max_output_tokens` to 120, confirm
  thinking is set low, and keep the server in the region closest to ElevenLabs.
