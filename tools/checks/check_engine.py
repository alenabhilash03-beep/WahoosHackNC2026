"""First-hour ElevenLabs check: is the Speech Engine set up the way the browser code expects?

    python tools/checks/check_engine.py          # report only
    python tools/checks/check_engine.py --fix    # also switch on any missing client events

Reads ELEVENLABS_API_KEY, SPEECH_ENGINE_ID and (optionally) PUBLIC_WS_URL from the environment,
then from server/.env.

What it checks:
  - client_events: the events ElevenLabs forwards to the browser. The call screen needs
    agent_response and user_transcript (onMessage), interruption (onInterruption),
    agent_response_correction (onAgentResponseCorrection) and audio. A missing one means that
    callback silently never fires. --fix adds the missing ones and keeps everything else.
  - overrides.first_message: must be on, or the browser's firstMessage override is ignored. It can
    only be set when the engine is created (speech_engine.update has no overrides argument in
    elevenlabs 2.71), so the fix is to create a new engine with server/scripts/create_engine.py.
  - ws_url: should equal PUBLIC_WS_URL, the wss:// address of your Speech Engine server.
"""
import asyncio
import os
import sys
from pathlib import Path

from elevenlabs import AsyncElevenLabs
from elevenlabs.types.conversation_config_input import ConversationConfigInput

ROOT = Path(__file__).resolve().parents[2]
NEEDED_EVENTS = [
    "conversation_initiation_metadata", "ping", "audio", "interruption",
    "user_transcript", "agent_response", "agent_response_correction",
]


def load_env() -> dict:
    env = {}
    path = ROOT / "server" / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    env.update({k: v for k, v in os.environ.items() if v})
    missing = [k for k in ("ELEVENLABS_API_KEY", "SPEECH_ENGINE_ID") if not env.get(k)]
    if missing:
        sys.exit(f"missing {', '.join(missing)} (set them or fill server/.env)")
    return env


async def main(fix: bool, client: AsyncElevenLabs | None = None) -> int:
    env = load_env()
    eleven = client or AsyncElevenLabs(api_key=env["ELEVENLABS_API_KEY"])
    engine_id = env["SPEECH_ENGINE_ID"]
    engine = await eleven.speech_engine.get(engine_id)
    cfg = engine.config
    ok = True
    print(f"engine {engine_id} ({cfg.name})")

    current = list((cfg.conversation.client_events if cfg.conversation else None) or [])
    missing = [e for e in NEEDED_EVENTS if e not in current]
    print(f"client_events: {current or '(none listed)'}")
    if missing:
        print(f"MISSING client events: {missing}")
        if fix:
            # Resend the current conversation settings (only fields the update accepts) so a PATCH
            # that replaces the whole object cannot reset anything; only client_events grows.
            dumped = cfg.conversation.model_dump(exclude_none=True) if cfg.conversation else {}
            conversation = {k: v for k, v in dumped.items() if k in ConversationConfigInput.model_fields}
            conversation["client_events"] = current + missing
            updated = await eleven.speech_engine.update(engine_id, conversation=conversation)
            now = list(updated.config.conversation.client_events or []) if updated.config else []
            still = [e for e in NEEDED_EVENTS if e not in now]
            print("fixed: client_events now", now) if not still else print("FIX DID NOT APPLY:", still)
            ok = ok and not still
        else:
            print("  run again with --fix to add them (nothing else changes)")
            ok = False
    else:
        print("PASS  every client event the call screen uses is enabled")

    first_message = bool(cfg.overrides and cfg.overrides.first_message)
    print(("PASS  " if first_message else "FAIL  ") + f"overrides.first_message = {first_message}")
    if not first_message:
        print("  the persona will ignore the browser's opener. Create a new engine with "
              "server/scripts/create_engine.py and update SPEECH_ENGINE_ID.")
        ok = False

    ws_url = cfg.speech_engine.ws_url if cfg.speech_engine else None
    expected = env.get("PUBLIC_WS_URL")
    if expected:
        same = ws_url == expected
        print(("PASS  " if same else "FAIL  ") + f"ws_url = {ws_url} (PUBLIC_WS_URL = {expected})")
        if not same:
            print("  fix: await eleven.speech_engine.update(SPEECH_ENGINE_ID, speech_engine={'ws_url': PUBLIC_WS_URL})")
            ok = False
    else:
        print(f"INFO  ws_url = {ws_url} (set PUBLIC_WS_URL to compare)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--fix" in sys.argv[1:])))
