# Phase 5 - Solo debate: record, grade, publish

**Goal:** a player picks a motion and a side, records a 60-second argument, sees the grader's report
in under 20 seconds, and can publish the speech to the arena. Phases 6 and 7 build on the
`speeches` rows and the grading worker made here.

| | |
| --- | --- |
| Lane | D |
| Hours | 12 to 18 |
| Needs | Phase 0 |

## Done when

- [ ] Recording on the deployed `/debate` page shows the overall score, letter, five factor rows
      (metric, value, bucket, score) and deductions in under 20 seconds
- [ ] Uploading the same file twice gives identical reports (grader cache)
- [ ] Publishing without consent returns 422; publishing with consent sets `published = true` and
      `moderation_status = 'approved'` for a normal speech
- [ ] `pytest server/tests/test_debate.py -q` passes, including a stubbed safety rejection
- [ ] Optional: the AI opponent plays a 45-second case with 3 tagged claims

## Files

| Path | New or changed | Purpose |
| --- | --- | --- |
| `server/scripts/seed_motions.py` | new | Inserts the motion list |
| `server/app/routes/speeches.py` | new | Upload, read, publish |
| `server/app/workers/debate.py` | new | `grade_speech(speech_id)`, shared with Phase 6 |
| `server/app/safety.py` | new | Gemini safety check before publishing |
| `server/app/voice.py` | new, optional | Voice mask through ElevenLabs Voice Changer |
| `server/app/opponent.py` | new, optional | AI opponent script and voice |
| `server/tests/test_debate.py` | new | Contract tests with stubs |
| `web/app/debate/page.tsx` | new | Motion, side, 60 s recorder, report |
| `web/components/Countdown.tsx` | new | Counts down to a server deadline (`lib/clock.ts`) |
| `web/components/DebateReport.tsx` | new | Renders a grader report |

## Steps

### 1. Motions

`server/scripts/seed_motions.py` inserts 5 to 8 motions with arguable sides, for example:

```python
MOTIONS = [
    "Should the frontier of AI be paced?",
    "Should college be free at public universities?",
    "Should homework be banned in elementary school?",
    "Should social media require age verification?",
    "Should remote work be the default for office jobs?",
]
```

Keep the grader's own test topic first: the grader's fixtures use it, which makes local testing easy.

### 2. Grading worker

`server/app/workers/debate.py`:

```python
import asyncio
import tempfile
from pathlib import Path

from .. import grading  # noqa: F401
from .. import storage
from ..db import one, supabase
from ..realtime import broadcast
from .postcall import to_wav
from grader import grade

CACHE = Path(__file__).resolve().parents[2] / "cache"
GRADE_SLOTS = asyncio.Semaphore(8)          # at most 8 speeches graded at once


async def grade_speech(speech_id: str) -> dict:
    sp = one("speeches", id=speech_id)
    motion = one("motions", id=sp["motion_id"])
    async with GRADE_SLOTS:
        with tempfile.TemporaryDirectory() as folder:
            wav = await asyncio.to_thread(to_wav, storage.get(sp["audio_path"]), folder)
            report = await asyncio.to_thread(grade, wav, motion["text"], sp["side"], None, str(CACHE))
    supabase.table("speeches").update({
        "report": report, "overall": report["overall"]["score"],
        "standards_id": report["inputs"]["standards_id"],
    }).eq("id", speech_id).execute()
    await broadcast(f"player:{sp['player_id']}", "speech_graded", {"speech_id": speech_id,
                                                                    "overall": report["overall"]})
    return report
```

`grade` returns `gradable: false` with a reason for under 40 words; show the reason, do not error.

### 3. Routes

`server/app/routes/speeches.py`:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .. import storage
from ..auth import current_player
from ..db import one, supabase
from ..safety import check_transcript
from ..workers.debate import grade_speech

router = APIRouter(prefix="/api/speeches", tags=["speeches"])


@router.post("", status_code=202)
async def upload(background: BackgroundTasks, motion_id: str = Form(...), side: str = Form(...),
                 audio: UploadFile = File(...), player_id: str = Depends(current_player)):
    if side not in ("yes", "no") or not one("motions", id=motion_id):
        raise HTTPException(422, "bad motion or side")
    row = supabase.table("speeches").insert({"player_id": player_id, "motion_id": motion_id, "side": side,
                                             "audio_path": "pending"}).execute().data[0]
    path = storage.put(f"speeches/{row['id']}.webm", await audio.read())
    supabase.table("speeches").update({"audio_path": path}).eq("id", row["id"]).execute()
    background.add_task(grade_speech, row["id"])
    return {"speech_id": row["id"], "status": "grading"}


@router.get("/{speech_id}")
def read(speech_id: str, player_id: str = Depends(current_player)):
    sp = one("speeches", id=speech_id)
    if not sp or sp["player_id"] != player_id:
        raise HTTPException(404)
    return {**sp, "audio_url": storage.signed_url(sp["audio_path"])}


class Publish(BaseModel):
    consent: bool
    voice_mask: bool = False


@router.post("/{speech_id}/publish")
async def publish(speech_id: str, body: Publish, player_id: str = Depends(current_player)):
    sp = one("speeches", id=speech_id)
    if not sp or sp["player_id"] != player_id:
        raise HTTPException(404)
    if not body.consent:
        raise HTTPException(422, "consent required to publish a recording")
    if not sp.get("report") or not sp["report"].get("gradable"):
        raise HTTPException(409, "only graded speeches can be published")
    verdict = await check_transcript(sp["report"]["transcript"])
    status = "approved" if verdict.allowed else "rejected"
    update = {"moderation_status": status, "published": verdict.allowed}
    if verdict.allowed and body.voice_mask:
        update["masked_audio_path"] = await mask_voice(sp["audio_path"], speech_id)   # step 5
    supabase.table("speeches").update(update).eq("id", speech_id).execute()
    return {"published": verdict.allowed, "moderation_status": status, "category": verdict.category}
```

### 4. Safety check

`server/app/safety.py`:

```python
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel

from .settings import settings

gemini = genai.Client(api_key=settings.gemini_api_key)


class Safety(BaseModel):
    allowed: bool
    category: Literal["ok", "harassment", "hate", "sexual", "self_harm", "dangerous", "personal_info"]


async def check_transcript(text: str) -> Safety:
    prompt = ("A student recorded this 60-second debate speech and wants to publish it for other students "
              "to judge. Allow normal argument, including strong opinions. Disallow harassment, hate, sexual "
              "content, self-harm, dangerous instructions or personal information about real private people.\n\n"
              f"<speech>\n{text}\n</speech>")
    r = await gemini.aio.models.generate_content(
        model=settings.judge_model, contents=prompt,
        config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                           response_schema=Safety))
    return r.parsed
```

### 5. Optional voice mask

ElevenLabs Voice Changer (speech-to-speech) re-voices the recording, so listeners judge the argument
and not the voice. Check that it keeps pitch movement before enabling it by default: grade a speech
and its masked copy and compare `pitch_variation_pct`.

`server/app/voice.py` (and add `from ..voice import mask_voice` to `routes/speeches.py`):

```python
from elevenlabs import AsyncElevenLabs

from . import storage
from .settings import settings

eleven = AsyncElevenLabs(api_key=settings.elevenlabs_api_key)
MASK_VOICE_ID = "<a neutral voice id from your ElevenLabs voice library>"


async def mask_voice(audio_path: str, speech_id: str) -> str:
    chunks = eleven.speech_to_speech.convert(voice_id=MASK_VOICE_ID, audio=storage.get(audio_path))
    data = b"".join([c async for c in chunks])
    return storage.put(f"speeches/{speech_id}.masked.mp3", data, "audio/mpeg")
```

Confirm the method name and the async-iterator return type in the installed `elevenlabs` package.

### 6. Optional AI opponent

`server/app/opponent.py`: Gemini writes a 45-second case for the other side with exactly 3 claims
(response schema `{script: str, claims: list[str]}`, each claim a sentence copied from the script),
ElevenLabs text-to-speech voices it, and the route returns `{audio_url, claims}`. For the player's
rebuttal, build a stand-in opponent report so `match_judge.clash` can score it:

```python
opponent_report = {"gradable": True, "evidence": {"argument": {"arguments": [{"claim": c} for c in claims]}}}
clash_score = clash(rebuttal_report, opponent_report)
```

### 7. Debate page

`/debate`: motion dropdown, yes/no toggle, a big Record button. Recording starts a 60-second
`Countdown` and stops itself at zero (`MicRecorder` from Phase 1). Upload with `FormData`
(`motion_id`, `side`, `audio`), subscribe to `player:{player_id}` for `speech_graded`, and poll
`GET /api/speeches/{id}` every 3 s as a fallback. `DebateReport` renders, from the report:

- `overall.score`, `overall.letter`
- for each factor: name, score, `weight_pct`, then each metric row: `label`, `value`, `unit`,
  `bucket`, `score`; then each deduction with `points_lost`
- evidence: thesis and conclusion found, each argument with its `status`, fallacies, emotional appeals
- "Publish to the arena" with a consent checkbox and a voice-mask toggle

### 8. Tests

`server/tests/test_debate.py` with stubs (no network): monkeypatch `check_transcript` to return
`allowed=False, category="harassment"` and assert the publish response is not published; assert
publish without consent is 422; assert `grade_speech` writes `overall` and `standards_id` when
`grade` is stubbed to return the repo's `softskills-grader/sample_report.json`.

## Pitfalls

- Never publish a speech that is not graded: Phases 6 and 7 assume every published speech has a
  report and a `standards_id`.
- The grader caches by audio hash, so the cache folder must persist between requests; on Fly, mount
  a volume at `/repo/server/cache` or accept re-grading after each deploy.
