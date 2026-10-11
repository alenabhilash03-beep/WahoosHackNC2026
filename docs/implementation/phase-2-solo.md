# Phase 2: make solo good

Implements Phase 2 of the current SpeakUp Build Plan. The archived `phase-2-rapport.md` describes a retired networking feature and is not used here.

The repository had no Phase 0 server or Phase 1 recording flow. This change supplies the prerequisites needed to exercise the solo card: public browser config, anonymous Supabase sessions, owner-scoped FastAPI routes, private recording storage, prompt seeds, upload/background grading, and a recorder with the 10-second countdown and 60-second limit.

## Acceptance map

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Every metric and deduction, including zeros | `web/js/card-model.js`, `card.js` render the report without dropping rubric rows | Sample report shows all 24 rows; tests cover unavailable measurements |
| Logos, Pathos, Delivery with evidence | Verified claims/support types/statuses, appeals, stance/structure, delivery subfactors and sources | Card tests and browser checks |
| Fix these first | `server/app/feedback.py`, `tips.py`: stable top-three loss attribution, authored tips | Sample losses sum to 4.275; highest is 2.625; unavailable metrics use the scorer's effective weights |
| Transcript highlights and click to play | Real Scribe word timings, filler phrases, long pauses, actual monotone ranges, signed audio | Timing tests and browser playback seek; text and punctuation remain intact |
| Retry deltas and card radar | Strictly earlier best graded take on the same prompt and side; later takes cannot change an old baseline | Server tests for ties, sides, chronology and missing factors |
| Me radar on live data | `progress.js` calls authenticated `GET /api/me`, filters prompts, renders first/best per part, provenance, gains, best overall, last ten scores | Browser verifies HTTP-backed demo and prompt filtering; no hardcoded example array in the page |
| Empty/failure states | Grading poll limit, expired session refresh, failed/ungradable cards, error retry, empty history, microphone cancellation | Route, client and recorder tests; desktop/mobile browser checks |
| Private practice | Server resolves tokens via `auth.get_user`; history and card queries include owner; private bucket URLs expire after 300 seconds | Auth/repository tests include cross-owner 404 and no URL signing |

Emphasis is shown as measured loudness variation. No word-level emphasis timestamp is invented when the report has none. Legacy reports without word timings remain readable and can play independently timed pause evidence. Unmeasured rubric values display “—” and cannot create tips or spurious deltas.

Point loss is attribution before factor clipping. A tip does not guarantee that the next recording recovers exactly that many points. The original grading rubric and calibration remain unchanged.

## Run locally

From the repository root, create and activate a Python 3.11+ virtual environment, then:

```sh
python -m pip install -r server/requirements-dev.txt -r softskills-grader/requirements.txt
```

For the saved-example viewer, set `DEMO_MODE=true` in your environment and run:

```sh
python -m uvicorn server.app.main:app --host 127.0.0.1 --port 8000
```

In PowerShell use `$env:DEMO_MODE='true'`; in a POSIX shell use `export DEMO_MODE=true`. Open `/practice.html`, `/card.html?id=demo-best`, or `/progress.html` on that server. This explicitly labeled local viewer uses synthetic buzz audio and canned timestamps; it rejects real recording uploads. It is restricted to loopback hosts, clients, and origins.

For real practice, use `server/.env.example` to create `server/.env`, set `DEMO_MODE=false`, supply Supabase and grader keys, enable anonymous sign-in, and apply migrations 0001, 0002, and 0003. Install FFmpeg for browser WebM/Ogg/MP4 decoding. WAV decoding uses Python alone. For GitHub Pages, set the public values in `web/config.js` and allow its exact origin in `CORS_ORIGINS`. Server deployment and persistent cache guidance are in `server/README.md`.

## Checks

```sh
node --test web/test/*.test.mjs
python -m pytest server/tests softskills-grader/tests/test_timing.py -q
python softskills-grader/tests/test_pipeline.py
python Arena/test_arena_ratings.py
python Arena/test_match_judge.py
```

Browser QA uses the local HTTP server on desktop and a 390-pixel mobile viewport. It checks 24 rubric rows, tip ordering, deltas, radar table, filler playback seek, Me history, prompt selection, too-short/missing cards, absence of horizontal overflow, microphone permission/cancel, the actual countdown and MediaRecorder upload/navigation. Authentication and upload are intercepted for the recording check; external services are not called. Screenshots are captured for visual inspection.

Run the repeatable browser check against the loopback demo server:

```sh
npm install --prefix tools/checks
npm --prefix tools/checks exec -- playwright install chromium
node tools/checks/check_solo_browser.cjs http://127.0.0.1:8000
```

Screenshots go to ignored `work/browser-qa/`. To use an installed Edge browser, set `BROWSER_CHANNEL=msedge` instead of downloading Chromium. Tests can resolve an existing Playwright installation through `PLAYWRIGHT_MODULE`. The static frontend still needs no build step.

## Live acceptance still required

No deployed Supabase, ElevenLabs or Gemini credentials were supplied for this implementation. Therefore a human microphone recording graded by those services, deployed storage/RLS checks, and latency below 20 seconds are **not verified**. After configuration, record a speech, inspect every rubric row and evidence playback, retry the same prompt and side, and confirm `/api/me` reflects both takes. Upload the same file twice and compare reports.

Phase 2c's team recordings, strong/weak separation, ear-rank agreement and actionable-tip calibration are a separate phase. The community gate remains closed until those checks pass. This change does not implement Phase 3 or 4.
