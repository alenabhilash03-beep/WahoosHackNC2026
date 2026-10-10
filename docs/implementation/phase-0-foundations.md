# Phase 0 - Foundations

**Goal:** every teammate can run the server and the web app locally, both are deployed, the database
exists, and a browser can sign in anonymously and call an authenticated API route.

| | |
| --- | --- |
| Lane | Everyone (split the steps) |
| Hours | 0 to 2 |
| Needs | Nothing |

## Done when

- [ ] `GET https://<api-host>/health` returns `{"ok": true}` from the deployed server
- [ ] The deployed web app shows "Signed in as <uuid>" after loading
- [ ] `GET /api/me` with that session's token returns the same uuid, and a `profiles` row exists
- [ ] A broadcast sent with `curl` (step 7) appears in the browser console
- [ ] All four existing test suites pass on a fresh clone (README, "Conventions")

## Steps

### 1. Accounts and keys (one person, 20 min)

1. **ElevenLabs:** create an API key (Settings, API keys). Put it in the team password manager.
2. **Gemini:** create a key in Google AI Studio. Confirm `gemini-3.8-flash` is listed on the
   [models page](https://ai.google.dev/gemini-api/docs/models).
3. **Supabase:** create a project. Copy the project URL, the anon key and the service-role key.
   - Authentication, Providers: turn on **Anonymous sign-ins**.
   - Authentication, Rate limits: anonymous sign-ins default to **30 per hour per IP**. Everyone at
     the venue shares one IP, so raise it to 1000 now, or the live demo stops letting people in.
4. **Hosting:** a Fly.io account for the server (it needs two public ports, see step 8) and a Vercel
   account for the web app.

### 2. Repo layout

```bash
git pull
mkdir -p server/app/routes server/app/workers server/scenarios server/scripts server/tests
touch server/app/__init__.py server/app/routes/__init__.py server/app/workers/__init__.py
```

### 3. Database

Apply `supabase/migrations/0001_init.sql`, either:

```bash
npx supabase login
npx supabase link --project-ref <ref>
npx supabase db push
```

or by pasting the file into the Supabase SQL editor. Check the Table editor shows 16 tables and the
Storage page shows a private bucket called `audio`.

### 4. Server dependencies

`server/requirements.txt`:

```text
fastapi==0.115.*
uvicorn[standard]==0.32.*
python-multipart
httpx
pydantic>=2
pydantic-settings
supabase>=2.32
elevenlabs>=2.71
google-genai>=2.29
pyyaml
numpy
scipy
soundfile
praat-parselmouth
requests
pytest
```

```bash
cd server && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r ../softskills-grader/requirements.txt
```

ffmpeg must be on the PATH (`brew install ffmpeg`, `apt install ffmpeg`).

### 5. Server core files

`server/app/settings.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_service_role_key: str
    elevenlabs_api_key: str = ""
    speech_engine_id: str = ""
    gemini_api_key: str = ""
    public_ws_url: str = ""                 # wss://<host>:8443/ws
    web_origin: str = "http://localhost:3000"
    persona_model: str = "gemini-3.8-flash"
    judge_model: str = "gemini-3.8-flash"
    signing_secret: str = "change-me"       # Phase 7 matchup tokens
    min_listen_factor: float = 0.9          # Phase 7: share of both speeches a judge must hear
    match_margin: float = 3.0               # Phase 6: overall points; re-measured in Phase 9


settings = Settings()
```

`server/app/db.py`:

```python
from supabase import Client, create_client

from .settings import settings

supabase: Client = create_client(settings.supabase_url, settings.supabase_service_role_key)


def one(table: str, **eq):
    q = supabase.table(table).select("*")
    for k, v in eq.items():
        q = q.eq(k, v)
    rows = q.limit(1).execute().data
    return rows[0] if rows else None
```

`server/app/auth.py`:

```python
from fastapi import Header, HTTPException

from .db import supabase


def current_player(authorization: str = Header(default="")) -> str:
    """Resolve the Supabase access token to a player id. Sync on purpose: FastAPI runs it in a thread."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        user = supabase.auth.get_user(authorization.removeprefix("Bearer ")).user
    except Exception as exc:                       # expired or forged token
        raise HTTPException(401, "invalid token") from exc
    supabase.table("profiles").upsert({"id": user.id}, on_conflict="id", ignore_duplicates=True).execute()
    return user.id
```

`server/app/realtime.py`:

```python
import httpx

from .settings import settings


async def broadcast(topic: str, event: str, payload: dict) -> None:
    """Send one Realtime broadcast. The browser listens with supabase.channel(topic)."""
    url = f"{settings.supabase_url}/realtime/v1/api/broadcast"
    key = settings.supabase_service_role_key
    body = {"messages": [{"topic": topic, "event": event, "payload": payload}]}
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(url, json=body, headers={"apikey": key, "Authorization": f"Bearer {key}"})
        r.raise_for_status()
```

`server/app/storage.py`:

```python
from .db import supabase

BUCKET = "audio"


def put(path: str, data: bytes, content_type: str = "audio/webm") -> str:
    supabase.storage.from_(BUCKET).upload(path, data, {"content-type": content_type, "upsert": "true"})
    return path


def get(path: str) -> bytes:
    return supabase.storage.from_(BUCKET).download(path)


def signed_url(path: str, seconds: int = 3600) -> str:
    res = supabase.storage.from_(BUCKET).create_signed_url(path, seconds)
    return res.get("signedURL") or res.get("signed_url")
```

`server/app/grading.py` (makes the repo's Python modules importable from the server):

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("softskills-grader", "conversation", "Arena"):
    sys.path.insert(0, str(ROOT / sub))

from grader import grade                                          # noqa: E402,F401
from netmaxx_scoring import score_session                         # noqa: E402,F401
from arena_ratings import Judgment, Speech, aggregate_motion      # noqa: E402,F401
from match_judge import Entry, Unit, elo_multi, judge_match, judge_series  # noqa: E402,F401
```

`server/app/main.py`:

```python
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import current_player
from .settings import settings

app = FastAPI(title="NetworkMaxx")
app.add_middleware(CORSMiddleware, allow_origins=[settings.web_origin, "http://localhost:3000"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/time")
def server_time():
    """Clients compute their clock offset from this before any countdown."""
    return {"now": datetime.now(timezone.utc).isoformat()}


@app.get("/api/me")
def me(player_id: str = Depends(current_player)):
    return {"player_id": player_id}

# Later phases: app.include_router(sessions.router), speeches, matches, judgments, me
```

`server/run.py` (Phase 1 adds the Speech Engine to the same event loop):

```python
import asyncio

import uvicorn

from app.main import app


async def main():
    api = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info"))
    await api.serve()


if __name__ == "__main__":
    asyncio.run(main())
```

`server/tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_health():
    assert TestClient(app).get("/health").json() == {"ok": True}
```

Run: `cd server && cp .env.example .env` (fill it in), `python run.py`, then `pytest -q`.

### 6. Web app

```bash
npx create-next-app@latest web --typescript --tailwind --app --eslint --no-src-dir --import-alias "@/*"
cd web && npm install @supabase/supabase-js @elevenlabs/react
```

`web/lib/supabase.ts`:

```ts
import { createClient } from "@supabase/supabase-js";

export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
);

export async function ensureSignedIn(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  if (data.session) return data.session.access_token;
  const { data: signed, error } = await supabase.auth.signInAnonymously();
  if (error || !signed.session) throw error ?? new Error("anonymous sign-in failed");
  return signed.session.access_token;
}
```

`web/lib/api.ts`:

```ts
import { ensureSignedIn } from "./supabase";

const BASE = process.env.NEXT_PUBLIC_API_URL!;

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await ensureSignedIn();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}
```

`web/lib/clock.ts` (every countdown uses server time):

```ts
import { api } from "./api";

let offsetMs = 0;
export async function syncClock() {
  const t0 = Date.now();
  const { now } = await api<{ now: string }>("/api/time");
  const t1 = Date.now();
  offsetMs = Date.parse(now) - (t0 + t1) / 2;
}
export const serverNow = () => Date.now() + offsetMs;
export const msUntil = (iso: string) => Math.max(0, Date.parse(iso) - serverNow());
```

Make `web/app/page.tsx` call `api("/api/me")` on load and show "Signed in as {player_id}".

### 7. Check broadcast end to end

Open the web app, run this in the browser console:

```js
supabase.channel("test:hello").on("broadcast", { event: "ping" }, (m) => console.log("got", m)).subscribe()
```

Then from a terminal:

```bash
curl -s -X POST "$SUPABASE_URL/realtime/v1/api/broadcast" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"topic":"test:hello","event":"ping","payload":{"ok":true}}]}'
```

The console must print `got` with `payload: {ok: true}`. If it does not, the topic naming differs;
fix it here, because Phases 2, 4, 6 and 7 all depend on it.

### 8. Deploy

`server/Dockerfile` (build from the repo root so the grader and Arena modules are inside the image):

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /repo
COPY softskills-grader/requirements.txt softskills-grader/requirements.txt
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt -r softskills-grader/requirements.txt
COPY . .
WORKDIR /repo/server
EXPOSE 8000 3001
CMD ["python", "run.py"]
```

`fly.toml` at the repo root, two public ports (443 for the API, 8443 for the Speech Engine WebSocket):

```toml
app = "networkmaxx-api"
primary_region = "iad"

[build]
  dockerfile = "server/Dockerfile"

[[services]]
  internal_port = 8000
  protocol = "tcp"
  [[services.ports]]
    port = 443
    handlers = ["tls", "http"]

[[services]]
  internal_port = 3001
  protocol = "tcp"
  [[services.ports]]
    port = 8443
    handlers = ["tls", "http"]
```

```bash
fly launch --no-deploy --copy-config
fly secrets set SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... ELEVENLABS_API_KEY=... GEMINI_API_KEY=... \
  WEB_ORIGIN=https://<web>.vercel.app PUBLIC_WS_URL=wss://networkmaxx-api.fly.dev:8443/ws
fly deploy
```

Web: import the repo in Vercel with root directory `web`, set `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL=https://networkmaxx-api.fly.dev`.

Fallback if Fly is blocked: run the server on a laptop and expose both ports with
`ngrok http 8000` and `ngrok http 3001`.

### 9. Continuous tests

`.github/workflows/tests.yml`:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: sudo apt-get install -y ffmpeg
      - run: pip install -r softskills-grader/requirements.txt numpy scipy soundfile praat-parselmouth pyyaml requests
      - run: python softskills-grader/tests/test_pipeline.py
      - run: python conversation/test_netmaxx_scoring.py
      - run: python Arena/test_arena_ratings.py
      - run: python Arena/test_match_judge.py
```

## Pitfalls

- `create_signed_url` has returned both `signedURL` and `signed_url` keys across library versions;
  `storage.signed_url` handles both.
- Do not add RLS policies "to make it work". The browser should not read tables; add a server route.
- Keep `.env` files out of git (`server/.env`, `web/.env.local`).
