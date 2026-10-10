"""First-hour Supabase check: anonymous sign-in, server-side token check, and Realtime broadcast.

Run from the repo root once the Supabase project exists (Phase 0, steps 1 and 7):

    pip install "supabase>=2.32" httpx
    python tools/checks/check_supabase.py

It reads SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_ANON_KEY (or
NEXT_PUBLIC_SUPABASE_ANON_KEY) from the environment, then from server/.env and web/.env.local.
The service-role variable may hold the legacy service_role JWT or a new sb_secret_ key; the anon
variable may hold the legacy anon JWT or a new sb_publishable_ key.

What it proves, in the order the app depends on it:
  1. A visitor can sign in anonymously with the public key (what web/lib/supabase.ts does).
  2. The server accepts that visitor's access token with auth.get_user (what server/app/auth.py does).
  3. That visitor can subscribe to a public channel, the way supabase.channel(topic) does.
  4. A broadcast sent exactly like server/app/realtime.py reaches it, on the same topic name.
  5. The single-message REST endpoint reaches it too (a fallback if 4 fails).
It also reports whether the old "Authorization: Bearer <server key>" header is accepted with your
key type. Exit code 0 means items 1 to 4 passed. The anonymous user it creates is deleted at the end.
"""
import asyncio
import os
import secrets
import sys
from pathlib import Path

import httpx
from supabase import acreate_client, create_client

ROOT = Path(__file__).resolve().parents[2]
WAIT_S = 6.0


def load_env() -> dict:
    env = {}
    for path in (ROOT / "server" / ".env", ROOT / "web" / ".env.local"):
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    env.update({k: v for k, v in os.environ.items() if v})
    url = env.get("SUPABASE_URL") or env.get("NEXT_PUBLIC_SUPABASE_URL")
    service = env.get("SUPABASE_SERVICE_ROLE_KEY")
    anon = env.get("SUPABASE_ANON_KEY") or env.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    missing = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE_KEY", service),
                              ("SUPABASE_ANON_KEY", anon)) if not v]
    if missing:
        sys.exit(f"missing {', '.join(missing)} (set them or fill server/.env and web/.env.local)")
    return {"url": url.rstrip("/"), "service": service, "anon": anon}


def report(ok: bool, what: str, fix: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {what}")
    if not ok and fix:
        print(f"      fix: {fix}")
    return ok


async def broadcast_like_server(url: str, key: str, topic: str, event: str, payload: dict) -> httpx.Response:
    """Same request as server/app/realtime.py: batch endpoint, key on the apikey header only."""
    body = {"messages": [{"topic": topic, "event": event, "payload": payload, "private": False}]}
    async with httpx.AsyncClient(timeout=5) as client:
        return await client.post(f"{url}/realtime/v1/api/broadcast", json=body, headers={"apikey": key})


async def broadcast_single(url: str, key: str, topic: str, event: str, payload: dict) -> httpx.Response:
    """Single-message endpoint: the topic and event go in the path, the body is the payload."""
    async with httpx.AsyncClient(timeout=5) as client:
        return await client.post(f"{url}/realtime/v1/api/broadcast/{topic}/events/{event}", json=payload,
                                 headers={"apikey": key})


async def broadcast_bearer(url: str, key: str, topic: str, event: str, payload: dict) -> httpx.Response:
    """The header style the first draft of the guide used: apikey plus Authorization: Bearer <same key>."""
    body = {"messages": [{"topic": topic, "event": event, "payload": payload, "private": False}]}
    async with httpx.AsyncClient(timeout=5) as client:
        return await client.post(f"{url}/realtime/v1/api/broadcast", json=body,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})


async def main() -> int:
    cfg = load_env()
    url, service_key, anon_key = cfg["url"], cfg["service"], cfg["anon"]
    print(f"project {url}")
    print(f"server key type: {'new sb_secret_' if service_key.startswith('sb_secret_') else 'legacy JWT'}; "
          f"public key type: {'new sb_publishable_' if anon_key.startswith('sb_publishable_') else 'legacy JWT'}\n")

    # 1. Anonymous sign-in with the public key, as the browser does.
    visitor = await acreate_client(url, anon_key)
    try:
        signed = await visitor.auth.sign_in_anonymously()
        token, visitor_id = signed.session.access_token, signed.user.id
        ok1 = report(True, f"anonymous sign-in (user {visitor_id})")
    except Exception as exc:
        report(False, f"anonymous sign-in: {exc}",
               "Authentication > Sign In / Providers: enable 'Allow anonymous sign-ins'. If it says rate "
               "limit, raise anonymous sign-ins under Authentication > Rate Limits.")
        return 1

    # 2. The server resolves the token, exactly like server/app/auth.py.
    server = create_client(url, service_key)
    try:
        resolved = server.auth.get_user(token).user
        ok2 = report(resolved.id == visitor_id and bool(resolved.is_anonymous),
                     f"server auth.get_user(token) -> {resolved.id} (is_anonymous={resolved.is_anonymous})",
                     "the server key belongs to another project, or the token expired; re-copy both keys")
    except Exception as exc:
        ok2 = report(False, f"server auth.get_user(token): {exc}",
                     "check SUPABASE_SERVICE_ROLE_KEY is this project's service_role or sb_secret_ key")

    # 3. Subscribe as the visitor to a public channel.
    topic = f"check:{secrets.token_hex(4)}"
    got: dict[str, dict] = {}
    arrived = asyncio.Event()
    subscribed = asyncio.Event()
    states: list[str] = []

    def on_ping(message):
        payload = message.get("payload", {})
        got[payload.get("via", "?")] = payload
        arrived.set()

    def on_state(state, err):
        states.append(f"{getattr(state, 'value', state)}{f' ({err})' if err else ''}")
        if getattr(state, "value", state) == "SUBSCRIBED":
            subscribed.set()

    channel = visitor.channel(topic)
    channel.on_broadcast("ping", on_ping)
    await channel.subscribe(on_state)
    try:
        await asyncio.wait_for(subscribed.wait(), 10)
        ok3 = report(True, f"subscribed to public channel '{topic}'")
    except asyncio.TimeoutError:
        ok3 = report(False, f"subscribe to '{topic}': states seen {states or 'none'}",
                     "Realtime > Settings: turn ON 'Allow public access' (when it is off, only private "
                     "channels work and every topic in this guide would need RLS on realtime.messages)")

    async def expect(via: str) -> bool:
        try:
            await asyncio.wait_for(_until(lambda: via in got, arrived), WAIT_S)
            return True
        except asyncio.TimeoutError:
            return False

    # 4. Broadcast like the server.
    ok4 = False
    if ok3:
        r = await broadcast_like_server(url, service_key, topic, "ping", {"via": "batch", "ok": True})
        ok4 = report(r.status_code in (200, 202) and await expect("batch"),
                     f"server-style broadcast (POST /realtime/v1/api/broadcast, apikey header) -> HTTP "
                     f"{r.status_code}, received={'batch' in got}",
                     f"HTTP {r.status_code}: {r.text[:200]}. If 2xx but not received, the topic names differ: "
                     "the server must send the bare topic ('session:<id>'), never 'realtime:session:<id>'.")

        # 5. Single-message endpoint.
        r = await broadcast_single(url, service_key, topic, "ping", {"via": "single", "ok": True})
        report(r.status_code in (200, 202) and await expect("single"),
               f"single-message endpoint (/api/broadcast/<topic>/events/<event>) -> HTTP {r.status_code}, "
               f"received={'single' in got}", "optional; the server uses the batch endpoint")

        # Informational: does the old Bearer header style work with this key type?
        r = await broadcast_bearer(url, service_key, topic, "ping", {"via": "bearer", "ok": True})
        works = r.status_code in (200, 202) and await expect("bearer")
        print(f"INFO  'Authorization: Bearer <server key>' as well: "
              f"{'accepted' if works else f'rejected (HTTP {r.status_code})'}; server/app/realtime.py "
              f"sends the apikey header only, which works with both key types")

    print(f"\nreceived payloads: {got}")
    try:
        await visitor.remove_channel(channel)
    except Exception:
        pass
    try:
        server.auth.admin.delete_user(visitor_id)
    except Exception as exc:
        print(f"note: could not delete the test user {visitor_id}: {exc}")
    passed = ok1 and ok2 and ok3 and ok4
    print("\nALL REQUIRED CHECKS PASSED" if passed else "\nSOME CHECKS FAILED (see fixes above)")
    return 0 if passed else 1


async def _until(condition, event: asyncio.Event) -> None:
    while not condition():
        event.clear()
        if condition():
            return
        await event.wait()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
