# SpeakUp

**Practice arguing out loud, and find out exactly how to get better.**
A HackNC 2026 project by team Wahoos.

SpeakUp is a pixel-art 3D debate arena. You walk on stage, get a yes-or-no question, look out
at a packed house, and speak for up to 60 seconds. A minute later you have a speech card that
grades your **logic, emotional appeal and delivery**, shows the evidence behind every number,
and tells you in plain words what to change next time. Then you can climb a leaderboard for
your difficulty, or share your take on the Daily Prompt and let the community react.

**Play it:** https://speak-up.fly.dev (sign in with GitHub)
Also served as a static copy on GitHub Pages: https://alenabhilash03-beep.github.io/WahoosHackNC2026/web/

**Two AI services carry the work:**

- **ElevenLabs Scribe** hears the speech: every word with its exact timing and confidence,
  fillers included. That is what makes pace, pauses, fillers, stumbles and pitch timing measurable.
- **Google Gemini** understands it: it maps the argument (thesis, claims, evidence,
  rebuttal, fallacies, emotional appeals), double-checks which side you argued, writes the
  questions, and turns the numbers on your card into one-line coaching.

Neither service hands out the grade. **Scores come from our code**, measured against a
research-based rubric, so the same recording always gets the same report.

---

## Contents

- [What a round looks like](#what-a-round-looks-like)
- [How ElevenLabs is used](#how-elevenlabs-is-used)
- [How Gemini is used](#how-gemini-is-used)
- [How a speech is graded](#how-a-speech-is-graded)
- [Community: Daily Prompt and reactions](#community-daily-prompt-and-reactions)
- [Architecture](#architecture)
- [Repository map](#repository-map)
- [Run it locally](#run-it-locally)
- [API](#api)
- [Tests and CI](#tests-and-ci)
- [Privacy and safety](#privacy-and-safety)
- [Further reading](#further-reading)

---

## What a round looks like

1. **The lobby.** A 3D theatre built from pixel-textured boxes: your speaker in a suit stands in
   the aisle, the audience faces the stage, a chiptune theme plays. Choose your moment:
   **Solo Speak** (your call) or the **Daily Prompt** (today's challenge for everyone).
2. **The walk.** Your speaker turns, walks down the aisle and climbs the stage steps
   (about three seconds, with knees, a natural stride and a camera that follows).
3. **Pick a challenge** (Solo Speak). Choose **Beginner, Intermediate or Expert**, then one of
   five random well-known categories, or type your own.
4. **The question.** As the speaker walks to the podium, **Gemini writes a yes-or-no question**
   for that category and level (about one second). Choose **ARGUE YES** or **ARGUE NO**.
5. **Through your eyes.** A five-second countdown (5, 4, 3, 2, 1) from the podium: the gaze
   sweeps across a full house and balcony, stage lights glaring, then settles down the centre
   aisle.
6. **Speak.** Up to 60 seconds. A bar along the bottom fills and turns from white to red;
   FINISH ends early. At least 40 words are needed for a grade.
7. **Applause.** The crowd claps (box hands and all) while the speech uploads. The speech card
   fades in over the stage as the applause dies away, and a fanfare plays for your grade:
   a loud victory for an A, a softer one for a B, a jingle for a C, a sad trombone for a D or F.
8. **The speech card.**
   - Overall score and letter, and if a critical failure capped it, the reason why.
   - **Logos, Pathos and Delivery** section scores, with changes against your previous best.
   - **Fix these first**: the three changes worth the most points, each with a tip.
   - The **speech radar**: this speech outlined over your previous best.
   - **Hear the evidence**: your transcript with fillers, long pauses, monotone stretches and
     emphasis highlighted. Click any word or highlight to play that moment.
   - **Every point, explained**: all 25 metrics as boxes. Open one for a **Gemini coaching
     line** ("Back up your claims with solid facts and examples."), then the numbers and the
     evidence behind them.
   - **Retry this prompt** goes straight back to the podium with the same question and side;
     **Return to lobby** goes home.
9. **Progress and leaderboards.** The progress page charts your first attempt against your best
   on every part of the speech. There are **three leaderboards, one per difficulty**, so a 90 on
   an expert question is never ranked below a 92 on a beginner one.

Everything on screen is pixel art: the Press Start 2P typeface, velvet and gold, hard shadows.
All sound (music, clicks, countdown beeps, applause, fanfares) is synthesised in the browser
with Web Audio. There are no audio files to ship or license, and a ♪ switch mutes it all.

---

## How ElevenLabs is used

**Speech-to-text with word-level timing is the foundation of every delivery metric.** We send
each recording to **ElevenLabs Scribe** (`scribe_v2`) once, from
[`softskills-grader/grader/transcribe.py`](softskills-grader/grader/transcribe.py).

**What we ask for, and why:**

| Setting | Value | Why it matters |
| --- | --- | --- |
| `timestamps_granularity` | `word` | Start and end time for every word: speaking rate, pauses, time used, and where each pitch measurement falls |
| `no_verbatim` | `false` | Keep "um", "uh", false starts and repeats. A cleaned transcript would hide exactly what we need to count |
| `temperature` / `seed` | `0` / `7` | Repeatable transcripts, so a regrade gives the same report |
| `language_code` | `en` | English-only practice |
| `diarize`, `tag_audio_events` | off | One speaker, no sound-event tags |

**What we get back and use.** Every word, with its start and end times and a log-probability
that we convert to a confidence. Those feed:

- **Pace:** words per minute, share of time spent in pauses, long pauses (2 s or more), share of
  the minute used.
- **Diction:** fillers per 100 words, stumbles (a word said twice in a row), vocabulary variety,
  and **clarity** (average recognition confidence).
- **Tone, with our own audio analysis:** the word timings tell us where each sentence ends, so
  we can measure rising pitch at the end of statements (uptalk), and they place monotone
  stretches against the words.
- **Language counts:** hedges ("I guess", "sort of"), reasoning connectives ("because",
  "therefore"), audience address ("you", "we") and emotion words, using our word lists.
- **The transcript itself** goes to Gemini for the argument analysis.
- **The speech card** uses the timings to highlight words and seek the audio to the exact moment.

**Making it fast and dependable.**

- **Caching by audio hash:** each transcript is stored under the hash of the audio, so a
  recording is never transcribed twice.
- **Typical speed:** a 60-second speech transcribes in about 1.5 s.
- **Hedging:** if a request is still running after 6 s, a second identical request is raced
  against it, and the first answer wins. A real failure, such as a bad key, is not sent twice.
- **Bounded waits:** requests time out after 5 s to connect and 30 s to answer, so a stalled
  connection can't hold a player for minutes.

---

## How Gemini is used

Gemini does four jobs. Each uses **structured JSON output** (a response schema) through the
Gemini Interactions API, and each falls back gracefully if Gemini is unavailable.

### 1. Mapping the argument (the core of Logos and Pathos)

[`softskills-grader/grader/extract.py`](softskills-grader/grader/extract.py), model `gemini-3.8-flash`, low thinking.

Gemini reads the transcript and the question and returns the **structure** of the argument,
never a score:

- **The speaker's position** in one sentence, then their **stance** as the literal answer to
  the question. For example, wanting to keep homework answers "no" to "Should homework be
  banned?".
- **Whether the speech is on topic** at all (yes, partly or no).
- **Thesis** and **conclusion**, as quotes.
- **Each claim**, with its **support type** (reasoning, example, statistic, authority, analogy,
  or none) and the supporting quote.
- **Counterargument** and **rebuttal**.
- **Reasoning fallacies:** straw man, false dilemma, slippery slope and others.
- **Manipulative appeals:** fear-mongering and personal attacks.
- **Emotional appeals:** personal story, vivid imagery, shared values, human stakes, rhetorical
  question, call to action.

**Every item must be a verbatim quote, and our code checks it.**
[`features.py`](softskills-grader/grader/features.py) looks for each quote in the ElevenLabs
transcript and throws away anything Gemini paraphrased or invented. It also drops duplicate
claims and supports that only restate the claim. Python then counts what survives, so Gemini
cannot raise a grade by making up material.

### 2. Verifying the side you argued

[`softskills-grader/grader/stance.py`](softskills-grader/grader/stance.py).

Arguing the wrong side caps a speech at 40, so that finding must be right. A second, independent
check runs **in parallel** with the main extraction:

- **First pass:** `gemini-3.5-flash-lite` (minimal thinking) classifies the stance and must back
  it with a quote that our code verifies.
- **Confirmation:** only when that pass says you argued the opposite of your chosen side,
  `gemini-3.8-flash` at medium thinking re-checks it. The penalty applies only if both agree and
  the quote is verified. Otherwise no side penalty is given.

### 3. Writing the questions

[`server/app/questions.py`](server/app/questions.py), `POST /api/questions`.

Gemini writes one yes-or-no debate question from the chosen category and difficulty:

- **Beginner:** everyday and concrete.
- **Intermediate:** school, work, society or policy.
- **Expert:** a genuine trade-off with strong arguments on both sides.

**The rules:**
- Fair to both sides, at most 20 words, starting with *Should / Is / Does…*.
- Our code checks the format. A free-text category is treated as a topic name only, never as
  instructions.
- If Gemini judges a category unsuitable, the player is asked for another.
- `gemini-3.5-flash-lite` goes first because it answers in about one second. Template questions
  keep play going if Gemini is down.

### 4. Coaching lines on the speech card

[`server/app/coaching.py`](server/app/coaching.py), `GET /api/speeches/{id}/coaching`.

One call per speech turns all 25 measurements into **one short, actionable sentence each**: no
scores or jargon, just what to do differently, or what to keep doing. Examples:

- "Directly address your listeners more often using 'you' or 'we'."
- "Mention the opposing viewpoint to strengthen your case."

The lines are cached per speech. If Gemini is unavailable, our hand-written tips are used.

### Keeping Gemini fast and reliable

Gemini's models vary in speed and availability under load, so every call goes through shared
reliability code ([`grader/http.py`](softskills-grader/grader/http.py)):

- **Model fallbacks:** grading tries `gemini-3.8-flash`, then `gemini-3.6-flash`, then
  `gemini-3.5-flash-lite`, and questions start from the fast model.
- **Several API keys:** `GEMINI_API_KEYS` adds keys from other Google Cloud projects, for more
  capacity.
- **Hedged requests:** if an answer is slower than usual (4 s for grading and coaching, 2 s for
  questions), a backup request races it.
- **Switching on rate limits and overload:** a rate limit (429) or "overloaded" (503) hands over
  to the next model at once. Plain server errors are retried with short waits.
- **Hard time limits:** requests time out after 5 s to connect and 30 s to answer, and one call's
  retries stop at 45 s.
- **Caching:** extractions are keyed by prompt version and request, so a speech is analysed once.

Measured during a Gemini slowdown (`gemini-3.8-flash` taking up to 35 s): questions arrived in
0.7–1.0 s, and full grades took 5.7–9.4 s, down from 12.5–28 s.

---

## How a speech is graded

```
recording ──► ElevenLabs Scribe ──► words + timings + confidence ─┐
    │                                                            ├─► our feature code ──► scoring engine ──► report
    └────────► our pitch & loudness analysis ─────────────────────┤        (verifies every
                                                                 │         Gemini quote)
transcript ──► Gemini: argument structure ─────────────────────────┤
           └─► Gemini: independent stance check ─────────────────┘
```

**Five factors, one weighted overall (0–100 with a letter):**

| Factor | Weight | Measured from |
| --- | --- | --- |
| Pace | 20% | Words per minute, pause time, time used, long pauses |
| Diction | 20% | Fillers, vocabulary variety, clarity, stumbles |
| Tone | 20% | Pitch variation, hedges, uptalk, monotone stretches |
| Logic | 25% | Supported arguments, thesis and conclusion, counterargument and rebuttal, connectives, fallacies, unsupported claims, side and topic |
| Emotion | 15% | Emotional appeals, audience address, emotion words, manipulative appeals |

**The rubric is research-based.** Each metric's thresholds come from published studies on
speaking rate, filler words, pitch variation, hedging, explicit conclusions and two-sided
arguments. Metrics are weighted by the strength of that evidence. The sources are in
[Research basis for the standards](softskills-grader/README.md#research-basis-for-the-standards),
and the whole rubric lives in one editable file:
[`softskills-grader/config/standards.yaml`](softskills-grader/config/standards.yaml).

**Critical failures cap the overall,** so great delivery can't carry a bad argument:

| Failure | Overall capped at |
| --- | --- |
| The speech does not address the question | 25 |
| Argued the opposite side (confirmed twice, see above) | 40 |
| No argument backed by a reason or evidence | 50 |
| Two or more fear-mongering or personal attacks | 50 |
| Far too slow, under 40% of the time used, extremely repetitive, or 15+ fillers per 100 words | 55 |
| Logic score below 50, a very flat voice, or constant hedging | 60 |

**Grades are deterministic.** Transcripts and Gemini annotations are cached per recording, and
scoring is pure code, so the same recording always gets a byte-identical report. Changing the
rubric regrades old speeches without calling any API.

---

## Community: Daily Prompt and reactions

The **Daily Prompt** is one shared question per day, midnight to midnight UTC:

- **Recording:** everyone answers the same question, recorded on stage like any other round.
- **Sharing is opt-in:** after grading you can choose **Share with the community**. Choosing a
  display name never shares audio by itself.
- **Reactions:** signed-in listeners play shared speeches and react with **Love, Like, Fire,
  Amazing or Downvote**. That's one reaction per person per speech, and you can't react to your own.
- **Two rankings** of the same shared speeches: **Highest graded** (the AI score) and
  **Community favorites** (positive reactions minus downvotes). Each positive reaction adds one;
  a downvote subtracts one. The community score is separate from the grade and total reaction count.
- **Review:** shared speeches stay hidden until approved, using
  [`server/scripts/review_community.py`](server/scripts/review_community.py) or
  `COMMUNITY_AUTO_APPROVE=true`.
- **Retention:** recordings are deleted shortly after the round ends, and grades stay.
- **Maintenance scripts:** rounds are created by
  [`manage_community_rounds.py`](server/scripts/manage_community_rounds.py), and expired audio
  is removed by [`cleanup_community_audio.py`](server/scripts/cleanup_community_audio.py).

The rules on who may see what (round open, entry shared, approved, graded, recording still
present) are enforced in the database, migration
[`0006_community_reactions.sql`](supabase/migrations/0006_community_reactions.sql).
Downvotes extend those rules in [`0007_community_downvotes.sql`](supabase/migrations/0007_community_downvotes.sql),
which preserves existing votes and must be applied before deploying the downvote backend and frontend.

---

## Architecture

```
Browser ── web/ (static HTML + JS modules, Three.js arena, Web Audio)
   │   GitHub sign-in through Supabase Auth; the browser holds only the public key
   │   Authorization: Bearer <access token>
   ▼
FastAPI server (Fly.io: one machine, persistent volume for caches)
   ├── softskills-grader ──► ElevenLabs Scribe (speech to text)
   │                    └──► Gemini (argument structure, stance check)
   ├── questions / coaching ──► Gemini
   ▼
Supabase: Postgres with row-level security, a private audio bucket, Auth
```

| Layer | Choice |
| --- | --- |
| Frontend | Static `web/`: plain JS modules, no build step. **Three.js** for the arena (instanced crowd of ~460 people with moods, blinks and clapping), Press Start 2P typography, **Web Audio** for all sound |
| Accounts | **Supabase Auth** with GitHub sign-in. The server resolves every token itself and never trusts a player id from the browser |
| API | **FastAPI** on **Fly.io** (`speak-up`), serving the site and the API from one origin. Grading runs in the background; the page polls the card |
| Data | **Supabase Postgres** (profiles, motions, speeches, community rounds, entries, reactions) with RLS and no browser policies; recordings in a **private** Storage bucket behind 5-minute signed links |
| AI | **ElevenLabs Scribe** for transcription and timing; **Google Gemini** for analysis, stance checks, questions and coaching |
| Hosting | Fly.io for the app; GitHub Pages serves a static copy that calls the same API |

**Local mode** (`LOCAL_MODE=true`) runs the whole game on one computer with no accounts.
Speeches and recordings are stored on disk, and the same repository interface as Supabase is
used, so switching modes is configuration only.

---

## Repository map

| Path | What it is |
| --- | --- |
| [`web/`](web/README.md) | The game: lobby and 3D arena (`js/arena.js`), the round (`practice.html`, `js/practice.js`, `js/recorder.js`), the speech card (`card.html`, `js/card.js`), progress and radar (`progress.html`, `js/radar.js`), leaderboards and community (`leaderboard.html`, `js/community-*.js`), sound (`js/sound.js`), sign-in (`login.html`, `js/auth.js`, `js/account.js`) |
| [`server/`](server/README.md) | FastAPI app (`app/main.py`), Supabase and local stores (`app/repository.py`, `app/local.py`), questions, coaching, leaderboards, community routes, feedback ("fix these first"), maintenance scripts, tests |
| [`softskills-grader/`](softskills-grader/README.md) | The grader: ElevenLabs transcription, audio analysis, Gemini extraction and stance checks, features, scoring, the rubric (`config/standards.yaml`), tests, `time_grade.py` for timing real runs |
| [`supabase/`](supabase/README.md) | Migrations `0001`–`0007` and database setup and verification |
| [`Arena/`](Arena/) | Crowd-rating math (`arena_ratings.py`: Bradley-Terry strengths with side-bias removal, rater weighting, Elo) and the parked two-player duel judge (`match_judge.py`) |
| [`tools/`](tools/checks/README.md) | Browser acceptance checks (Playwright), Supabase checks, database constraint tests |
| [`Dockerfile`](Dockerfile), [`fly.toml`](fly.toml) | One container: the API plus the static site |
| [`Build Plan.md`](Build%20Plan.md), [`docs/`](docs/) | Plans, specs and implementation notes |

---

## Run it locally

**Local mode** gives the full game with real grading, no accounts and no database. You need
Python 3.11 or newer, `ffmpeg` on your PATH (for browser WebM/MP4 audio), and an ElevenLabs key
and a Gemini key.

```sh
python -m venv .venv && source .venv/bin/activate
python -m pip install -r server/requirements.txt -r softskills-grader/requirements.txt
cp softskills-grader/.env.example softskills-grader/.env   # add ELEVENLABS_API_KEY and GEMINI_API_KEY
LOCAL_MODE=true python -m uvicorn server.app.main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000 in Chrome and allow the microphone at the podium. Local mode only
answers requests from this computer.

**Accounts mode** (as deployed): copy `server/.env.example` to `server/.env`, then set the
Supabase URL, service-role and anonymous keys, and the ElevenLabs and Gemini keys. Apply the
migrations in order and enable GitHub sign-in, as described in [`supabase/README.md`](supabase/README.md)
and [`docs/implementation/social-accounts.md`](docs/implementation/social-accounts.md).

**Grade a recording from the command line,** without the website:

```sh
cd softskills-grader
python grade.py speech.m4a --topic "Should homework be banned in elementary school?" --side yes
python time_grade.py speech.m4a --runs 3     # timings of every API call
```

**Deploy:** from the repository root, `fly deploy` builds the Dockerfile and ships the API and
site together (see [`server/README.md`](server/README.md)).

---

## API

All `/api/` routes need `Authorization: Bearer <token>`. The server derives the player from
the token.

| Route | Purpose |
| --- | --- |
| `GET /api/prompts` | Prompts available to practise |
| `POST /api/questions` | Gemini writes a yes-or-no question for `{category, difficulty}` |
| `POST /api/speeches` | Upload a recording (`motion_id`, `side`, `audio`); grading runs in the background |
| `GET /api/speeches/{id}` | The owner's speech card: report, signed audio link, top fixes, deltas |
| `GET /api/speeches/{id}/coaching` | Gemini's one-line coaching per metric |
| `POST /api/speeches/{id}/retry` | Grade a saved recording again after a failure |
| `GET /api/me`, `POST /api/me/name` | Progress history; display name for the boards |
| `GET /api/leaderboard` | Three boards: beginner, intermediate and expert |
| `GET /api/community/round` | The current Daily Prompt round and your entry |
| `POST /api/community/entries` | Share a graded speech; `/entries/{id}/visibility` withdraws or re-shares |
| `GET /api/community/leaderboard` | Highest graded or Community favorites, paginated |
| `GET /api/community/speeches/{id}/audio` | A short-lived link to a shared recording |
| `POST /api/community/speeches/{id}/reaction` | Set, change or remove your reaction |

---

## Tests and CI

```sh
python -m pytest server/tests softskills-grader/tests -q   # API, grader, scoring, retries, community
python Arena/test_arena_ratings.py && python Arena/test_match_judge.py
node --test web/test/*.test.mjs                            # card, radar, recorder, leaderboard, community models
```

At the time of writing: **168 API and grader tests pass** (10 more need a live Supabase), the
crowd-rating checks pass, and the duel judge passes 27/27.

GitHub Actions runs three workflows on every change:

- **Application checks:** the Python tests and the crowd and duel checks.
- **Database foundation:** applies every migration to a disposable Supabase, then tests
  constraints, RLS, concurrent display names and a real SDK round trip, including private audio,
  retry claims, boards and community reactions.
- **Community frontend:** the web unit tests plus a two-session Playwright browser check.

---

## Privacy and safety

- **Keys stay on the server.** The browser holds only the public Supabase key and its own
  session. Service-role, ElevenLabs and Gemini keys never reach it.
- **Practice is private.** Speeches, transcripts and reports are visible to their owner only.
  Recordings sit in a private bucket and are reached through 5-minute signed links.
- **The leaderboards show only** a chosen display name, score and letter.
- **Community sharing is opt-in,** reviewed before anyone can hear it, and deleted after the
  round. Community responses never include reports, transcripts, storage paths or who reacted.
- **Quotes are verified.** Gemini's quotes are checked against the transcript before they count,
  and a wrong-side penalty needs two models to agree.
- **Gemini treats a typed category as a topic name, never as instructions,** and refuses
  unsuitable topics.

---

## Further reading

- [Grader and rubric, with research sources](softskills-grader/README.md)
- [Server: modes, deployment and API details](server/README.md)
- [Web: pages, arena, sound and audience](web/README.md)
- [Database setup and verification](supabase/README.md)
- [Community reactions plan](docs/plans/2026-10-11-community-reactions.md)
- [Build plan](Build%20Plan.md) and [handoff notes](HANDOFF.md)

The two-player duel is parked (see the end of the Build Plan; `Arena/match_judge.py` already
scores it). The earlier NetworkMaxx concept, a voice-conversation game, is archived in
[`docs/implementation/archive/`](docs/implementation/archive/).
