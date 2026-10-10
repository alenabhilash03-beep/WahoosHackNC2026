# &#32;Build Plan

Oct 10, 2026 · @Kevin Jiang

## Summary

NetworkMaxx is a voice game: you talk to an AI persona that has a hidden goal, a chess-style rapport bar swings after every turn, and you finish with a player card where every number is either measured from your audio or judged by Gemini against a fixed rubric with a quote as evidence.

The core loop:

1. Pick a scenario: a persona, a hidden win condition ("get the referral") and a few planted details to test listening.
2. Talk for 2 to 4 minutes. The rapport bar moves each turn, and the persona warms up or cools off with it.
3. Hang up. Analysis runs in about 10 to 20 seconds.
4. See the player card and a replay timeline with flagged moments ("0:42 you interrupted", "1:15 great follow-up").
5. Retry the same scenario to beat your score and climb ranks.

A second loop, the **Debate Arena**, grades 60-second arguments with your repo's grader and lets the community judge them head to head.

Assumptions behind this plan: a 24 to 36 hour hackathon, a team of 3 to 4, a laptop web app used with headphones, English only. Stack: Next.js on the front, a Python FastAPI service for the game server and audio analysis, Supabase for storage, ElevenLabs for voice, Gemini for the persona brain and the judge.

## Architecture

The browser talks to ElevenLabs for voice, ElevenLabs hands each transcript to your game server, and the game server owns everything that makes it a game: persona, rapport, scoring and the card.

&#91;embedded content: system architecture · 7 components, live and post-call paths\]

The right half of the server runs every turn while you talk; the left half runs once after you hang up. Supabase sits between the server and the browser so rapport and results arrive without polling.

| Layer | Choice | Why |
| --- | --- | --- |
| Frontend | Next.js, React, Tailwind, `@elevenlabs/react` | The SDK hook handles the voice session; Tailwind is fast for the card and eval bar |
| Live voice | ElevenLabs Speech Engine | Speech-to-text, voice and turn-taking handled for you, while your server keeps the LLM and the game state |
| Post-call transcript | ElevenLabs Scribe | Word-level timestamps, which every timing metric needs |
| LLM | Gemini, Flash tier for anything live | Persona replies and the rapport judge are on the latency path; labeling and embeddings run after the call |
| Server | Python, FastAPI | praat-parselmouth is Python, and the Speech Engine Python SDK plugs into FastAPI |
| Data | Supabase Postgres, Storage, Realtime | One service for tables, audio files and live push |
| Hosting | Vercel for the frontend; Fly.io or Render for the server | Speech Engine needs a public `wss://` URL, so deploy the server early |

## End-to-end pipeline

The pipeline has two halves: a live loop that runs every turn (target under 1.5 s from you stopping to the persona speaking) and a post-call pass that builds the card (target under 20 s). ElevenLabs [Speech Engine](https://elevenlabs.io/docs/overview/capabilities/speech-engine) is the key choice: ElevenLabs handles speech-to-text, text-to-speech, turn-taking and interruptions, while your own server runs the LLM, so the game state lives in your code.

**Live loop, every turn**

1. **Start round.** Browser calls `POST /api/sessions` with a scenario id. Server creates the session row and returns a WebRTC conversation token from `get_webrtc_token` for the Speech Engine id.
2. **Record and connect.** Browser starts a `MediaRecorder` on the mic (headphones on) and stamps `t0`, then calls `startSession` with the token and the persona's opener as `firstMessage`. It posts the conversation id back so the server can map it to the game session.
3. **Log persona timing.** Browser records each persona speaking interval from mode-change events on the same `t0` clock. These intervals are what make latency, interruptions and talk share measurable later.
4. **User speaks.** ElevenLabs transcribes and calls the server's `on_transcript` with the full history.
5. **Server, in parallel:**
   1. Streams the persona reply from Gemini, with the current rapport band written into the system prompt, back through `session.send_response`.
   2. Runs the rapport judge on the last exchange, adds the deterministic nudges, updates rapport, and broadcasts it on a Supabase Realtime channel.
   3. Checks win and lose conditions.
6. **Browser** animates the eval bar and shows the move badge ("Great", "Inaccuracy") for that turn.

**Post-call pass**

1. Browser stops the recorder and uploads `user.webm` plus the persona intervals to `POST /api/sessions/{id}/finish`.
2. Server converts to 16 kHz mono WAV with ffmpeg.
3. ElevenLabs Scribe transcribes the mic track with word timestamps.
4. `netmaxx_scoring.py` computes PACE, DELIVERY, CONFIDENCE, LISTENING and FLOW plus the event list.
5. Gemini judge scores CONTENT with evidence quotes, and the listener test checks whether the main point landed.
6. Server assembles the card, rank change and timeline, saves it, and broadcasts `results_ready`.
7. Browser plays the card reveal, then the replay timeline synced to the audio.

**Browser (React)**

```tsx
import { useConversation } from "@elevenlabs/react";

const t0 = useRef(0);
const personaTurns = useRef<{ start: number; end: number; text: string }[]>([]);
const now = () => (performance.now() - t0.current) / 1000;

const conversation = useConversation({
  onModeChange: ({ mode }) => {               // verify event shape in SDK reference
    if (mode === "speaking") personaTurns.current.push({ start: now(), end: now(), text: "" });
    else if (personaTurns.current.length) personaTurns.current.at(-1)!.end = now();
  },
  onMessage: ({ message, source }) => {
    if (source === "ai" && personaTurns.current.length) personaTurns.current.at(-1)!.text += message;
  },
});

async function startRound(scenario) {
  const mic = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, autoGainControl: false, noiseSuppression: false },
  });                                          // AGC off keeps loudness honest
  const recorder = new MediaRecorder(mic, { mimeType: "audio/webm;codecs=opus" });
  recorder.start(1000);
  t0.current = performance.now();

  const { sessionId, token } = await api.post("/api/sessions", { scenarioId: scenario.id });
  const conversationId = await conversation.startSession({
    conversationToken: token,
    overrides: { agent: { firstMessage: scenario.opener } },
  });
  await api.post(`/api/sessions/${sessionId}/bind`, { conversationId });
  supabase.channel(`session:${sessionId}`)
    .on("broadcast", { event: "rapport" }, ({ payload }) => setRapport(payload))
    .subscribe();
}
```

**Game server, live (Python, FastAPI + Speech Engine)**

```python
from google import genai
from google.genai import types

gemini = genai.Client()
PERSONA_MODEL = "gemini-3.8-flash"     # newest stable Flash as of Oct 2026

async def on_transcript(transcript, session):
    game = games.by_conversation(session.conversation_id)
    contents = [types.Content(role="model" if m.role == "agent" else "user",
                              parts=[types.Part(text=m.content)]) for m in transcript]

    asyncio.create_task(update_rapport(game, transcript))   # applies from the next turn

    stream = await gemini.aio.models.generate_content_stream(
        model=PERSONA_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=persona_prompt(game.scenario, game.rapport),
            temperature=0.8, max_output_tokens=160),
    )

    async def chunks():
        async for c in stream:
            if c.text:
                yield c.text

    await session.send_response(chunks())
```

**Post-call worker (Python)**

```python
from elevenlabs import ElevenLabs
from netmaxx_scoring import score_session

eleven = ElevenLabs()

def finish(session_id, webm_path, persona_turns):
    wav = to_wav(webm_path)                       # ffmpeg -ac 1 -ar 16000
    stt = eleven.speech_to_text.convert(
        file=open(wav, "rb"), model_id="scribe_v2", no_verbatim=False,   # keep fillers
        timestamps_granularity="word", tag_audio_events=True)
    words = [w.model_dump() for w in stt.words if w.type == "word"]

    scenario = load_scenario(session_id)
    measured = score_session(wav, {
        "user_words": words, "agent_turns": persona_turns,
        "hooks": scenario.hooks, "key_sentence": load_session(session_id).declared_main_point})
    judged = judge_content(words, persona_turns, scenario)      # Gemini, next section
    card = assemble_card(measured, judged, scenario.weights)
    db.save_card(session_id, card)
    realtime.broadcast(f"session:{session_id}", "results_ready", {"card_id": card.id})
```

Things to verify in hour 1, because the plan depends on them: the exact client event names and payloads for mode changes and messages, whether `startSession` returns the conversation id in your SDK version. Filler words are settled: Scribe keeps "um" and false starts while no\_verbatim stays false, as your repo's grader already sets.

## The science: what a good conversation measurably looks like

Every measured stat on the player card traces to a published finding. Thresholds that the research does not pin down are marked as calibration choices you tune on your own recordings.

| Finding | Evidence | What we measure | How it scores |
| --- | --- | --- | --- |
| Asking follow-up questions makes people like you | Across 3 studies, including speed dating, people who asked more questions, especially follow-ups, were better liked and got more second dates ([Huang et al. 2017](https://dash.harvard.edu/handle/1/42704282)) | Follow-up questions: a user question that reuses content words from the persona's previous turn | Count and rate; the biggest single Listening input |
| Fast replies signal that two people "click" | Faster response times predicted enjoyment and connection, and outside listeners heard faster exchanges as more connected ([Templeton et al. 2022, PNAS](https://pmc.ncbi.nlm.nih.gov/articles/PMC8794835)) | Gap between the persona finishing and you starting | Median gap; under 0.7 s full credit, over 2.5 s zero (calibration) |
| Overlap is avoided and silence is minimized in every language tested | 10-language study of turn-taking ([Stivers et al. 2009, PNAS](https://forms.mpi.nl/node/50939)) | Interruptions: you start while the persona is talking and hold the floor for over 1 s | Each one costs points; short "mm-hmm" overlaps are counted as backchannels, not interruptions |
| Speaking up gets you liked more, not less | People assigned to talk 30 to 40% of the time were liked less than those at 50% or more; liking rose with talk time ([Hirschi, Wilson & Gilbert 2022](https://dtg.sites.fas.harvard.edu/HIRSCHI%20WILSON%20GILBERT%202022.pdf)) | Your share of total talk time | Low under 40%, full at 50 to 70%, gentle decline past 75% (untested, calibration) |
| People who mirror each other's function words connect | Pairs with above-median language style matching wanted future contact 33.3% of the time vs 9.1% below the median ([Ireland et al. 2011](https://pubmed.ncbi.nlm.nih.gov/21149854/)) | Language style matching (LSM) between you and the persona across 9 function-word categories | LSM 0 to 1, mapped to 0 to 100 |
| Speech rate trades competence against warmth | Faster speech raised competence ratings linearly; warmth peaked at normal rate ([Smith et al. 1975](https://sonicfield.org/library/effects-of-speech-rate-on-personality-perception)). Moderately fast callers persuaded best; very fast and very slow did worst ([Benki et al. 2011](https://iab.de/publication/?id=1927064)) | Words per minute, overall and per turn | Inverted U: in-band 140 to 190 wpm (calibration), plus a bonus for matching the persona's rate |
| A few short pauses beat perfect fluency | Callers who paused moderately did best; perfectly fluent ones did worst ([Benki et al. 2011](https://iab.de/publication/?id=1927064)) | Silent pauses of 250 ms or more inside your turns, per minute | Inverted U; zero pauses is penalized |
| Varied pitch sounds smarter and warmer | Hearing a pitch made recruiters rate candidates more competent and hireable than reading it ([Schroeder & Epley 2015](https://www.psychologicalscience.org/journals/psychological-science/0956797615572906/)); more intonation was rated more benevolent ([Brown et al. 1973](https://scholarsarchive.byu.edu/facpub/5973)); charismatic speakers show a wider pitch range ([Niebuhr 2020](https://cadernos.abralin.org/index.php/cadernos/article/view/272)) | Pitch spread in semitones around your own median | Inverted U: monotone is penalized, and so is overacting, since heavy variation backfired for some callers (Benki) |
| Hedges, fillers and question-like endings cost credibility | A "powerless" style (hedges, hesitations, intensifiers, rising intonation on statements) made witnesses less credible and less persuasive ([Erickson et al. 1978](https://scholars.duke.edu/publication/784538)) | Hedges and fillers per 100 words; share of statements ending on a rise | Fewer is better; feeds the Confidence stat |

What we deliberately do not score: your natural pitch level, accent, or absolute loudness. Lower voices are rated as more competent ([Klofstad et al. 2012](https://today.duke.edu/2012/03/andersonvote)), but pitch level is mostly anatomy, and forcing it down with vocal fry is rated less competent and less hireable ([Anderson et al. 2014](https://pmc.ncbi.nlm.nih.gov/articles/PMC4037169)). Loudness depends on the mic. Every voice metric is measured relative to your own baseline.

## Delivering the main message

The instinct is half right: slowing down and lifting your pitch work as local emphasis on the key phrase, but a slow, deep voice across the whole conversation reads as less competent. So we score contrast against your own baseline, not absolute level.

| Hypothesis | What the evidence says | What we score instead |
| --- | --- | --- |
| Use a deep tone | Lower voices are heard as stronger and more competent ([Klofstad et al. 2012](https://today.duke.edu/2012/03/andersonvote)), and successful phone invitations had lower median pitch ([Benki et al. 2011](https://iab.de/publication/?id=1927064)). But in a follow-up, lower voices were not more persuasive on policy appeals ([Klofstad & Anderson](https://sciencetrends.com/voice-pitch-influences-voters-but-do-we-pick-better-leaders-as-a-consequence)), and faking it with vocal fry backfires ([Anderson et al. 2014](https://pmc.ncbi.nlm.nih.gov/articles/PMC4037169)) | Statements that end level or falling, not rising like a question ([Erickson et al. 1978](https://scholars.duke.edu/publication/784538)) |
| Speak at a slow pace | Slowing the whole voice lowered competence ratings ([Smith et al. 1975](https://sonicfield.org/library/effects-of-speech-rate-on-personality-perception)); moderately fast speech persuaded best (Benki) | Overall pace in band, plus a local slowdown on the key phrase only |
| Go higher in pitch to grab attention | Right, as a local accent: a contrastive pitch accent improved listeners' memory for the accented item ([Fraundorf et al. 2010](https://www.ideals.illinois.edu/items/14788)), and words after a silent pause were recognized more often later ([MacGregor et al. 2010](https://www.stir.ac.uk/research/hub/publication/789589)) | A pitch lift on the key phrase relative to your median, set up by a pause just before it |

The Main Message score runs five checks on one sentence, worth 20 points each:

1. **Found:** the key sentence is the one closest to the main point you declared before the round. The engine matches by word overlap (Jaccard 0.3 or higher); swap in Gemini embeddings (cosine 0.6) if paraphrased points get missed. In modes without a declared point, Gemini extracts your central claim once and we match against that.
2. **Set up:** a silent pause of at least 300 ms right before it.
3. **Lifted:** its pitch peak is at least 2 semitones above your median, or its pitch range is at least 1.25 times your typical phrase range.
4. **Slowed:** its syllable rate is at most 0.9 times your median rate.
5. **Clean and landed:** no filler or hedge inside it, and the final pitch is level or falling.

Then a listener test: after the call, Gemini plays the persona and answers "In one sentence, what was their main point?" If that answer's embedding is within cosine 0.7 of your declared point, the message landed. The number thresholds above are starting calibration values; record 10 practice rounds from your team and adjust them so a deliberately good and a deliberately flat delivery land far apart.

## Measured metrics: formulas and scoring curves

Five of the six card stats come from `netmaxx_scoring.py`, a single Python file (praat-parselmouth + numpy) that turns the mic recording plus word timestamps into scores with no LLM involved. It passed 16 of 16 checks on a synthetic conversation, including an identical-output rerun.

Every raw number becomes 0 to 100 through one of three curves: a trapezoid band (zero outside, full credit inside a sweet spot), lower-is-better, or higher-is-better.

```latex
\text{band}(x) = 100 \cdot \begin{cases} 0 & x \le a \text{ or } x \ge d \\ \frac{x-a}{b-a} & a < x < b \\ 1 & b \le x \le c \\ \frac{d-x}{d-c} & c < x < d \end{cases}
```

| Metric | How it is computed | Curve (starting calibration) | Feeds |
| --- | --- | --- | --- |
| Pace | Words / minutes of your speech; 75% absolute band, 25% ratio to the persona's wpm | band 100 / 140 / 190 / 240 wpm; ratio band 0.7 / 0.9 / 1.2 / 1.5 | PACE |
| Pauses | Word gaps of 0.25 s or more inside your turns, per minute; gaps of 2 s or more flagged | band 0.5 / 3 / 12 / 25 per min | PACE |
| Pitch variation | Std of voiced pitch in semitones around your own median (octave jumps over 12 st dropped) | band 1.0 / 2.5 / 5.5 / 8.0 st | DELIVERY |
| Trailing off | Sentences over 1 s whose last quarter is more than 6 dB quieter than the rest | share: full at 15% or less, zero at 50% | DELIVERY |
| Main message | The 5-check score from the previous section | 0 to 100 | DELIVERY |
| Fillers | um, uh, er, erm, ah, hmm per 100 words | full at 2 or less, zero at 8 | CONFIDENCE |
| Hedges | "kind of", "sort of", "maybe", "I guess", "probably" and similar per 100 words | full at 1.5 or less, zero at 6 | CONFIDENCE |
| Question-like endings | Statements (no question word at the start) whose last 0.5 s rises more than 2 st | share: full at 15% or less, zero at 50% | CONFIDENCE |
| Follow-ups | Your questions that share a content word with the persona's last turn, or refer back to it ("how was that?") | share of your turns with one: full at 35% | LISTENING (40%) |
| Planted hooks | Hooks the persona mentioned that you later brought up | % recalled | LISTENING (30%) |
| Interruptions | You start more than 0.3 s into a persona turn and talk 1 s or more with 3+ words; shorter overlaps are backchannels | per 10 min: full at 1, zero at 8 | LISTENING (20%) |
| Style matching | LSM across 9 function-word categories (open word lists standing in for the licensed LIWC ones) | higher-is-better 0.6 to 0.9 | LISTENING (10%) |
| Response latency | Median gap from persona finishing to you starting, first reply only | full at 0.7 s or less, zero at 2.5 s | FLOW |
| Talk share | Your speech time / total speech time | band 20 / 50 / 70 / 85% | FLOW |

Two details that keep this honest:

- **Questions are detected from words, not punctuation.** Speech-to-text models often add a "?" because the voice rose, which would hide uptalk. So for the rising-ending check, a sentence counts as a real question only if it opens with a question word or ends with a tag like "right"; everything else is judged as a statement.
- **Every stat ships with its evidence.** The engine returns raw metrics, sub-scores and a timestamped event list (filler at 21.9 s, rising ending at 23.1 s, interruption at 28.0 s, follow-up on "Lisbon" at 4.5 s). The replay timeline draws straight from that list.

The sixth stat, CONTENT, is judged by Gemini (next section). Overall rating = a weighted mean of the six stats, with weights set per mode.

## Grader comparison: your repo vs netmaxx\_scoring.py

Your repo's [softskills-grader](https://github.com/alenabhilash03-beep/WahoosHackNC2026) is the better-built chassis: an editable rubric, cached API results and 20 passing tests. `netmaxx_scoring.py` measures pitch more accurately and covers conversations. The merged grader keeps your repo's architecture and ports in the parts that won below.

**Head-to-head on the same audio.** Both graders ran on your repo's known-truth fixture (12 sentences, 2 that end on a rise, true pitch spread 2.04 semitones) and on a synthetic perfectly flat voice.

| Test | Correct answer | Your repo | netmaxx\_scoring |
| --- | --- | --- | --- |
| Pitch spread, your fixture | 2.04 st | 2.20 st | 2.04 st |
| Pitch spread, perfectly flat voice | about 0 st | 0.65 st | 0.02 st |
| Rising endings found, normal punctuation | 2 | 2 | 2 |
| Rising endings found when speech-to-text writes "?" on them | 2 | 0 | 2 |
| Rising endings with no punctuation at all | measurable | not measured | measured on whole turns, unreliable |
| Own test suite | all pass | 20 of 20 | 16 of 16 |

The flat-voice row matters: your monotone bucket starts below 1.5 st, so a tracker that reads 0.65 st of jitter on a flat voice pushes a real monotone speaker toward the next bucket. The question-mark row matters because speech-to-text models often punctuate a rising statement as a question, which hides exactly the uptalk you want to catch.

| Criterion | Your repo | netmaxx\_scoring | Merged grader takes |
| --- | --- | --- | --- |
| Rubric editable without code | YAML buckets, weights, deductions; validated at load; rubric hash on every report | Hard-coded Python dict | Your repo |
| Same recording, same report | Scribe and Gemini results cached by audio hash; tested in a fresh process | Deterministic only once the transcript is fixed | Your repo |
| Explaining a score | Every bucket and every deducted point listed, plus a letter grade | Sub-scores and a timestamped event list | Both: your deduction trail plus the timeline events |
| Argument analysis | Thesis, claims with support type, counterargument and rebuttal, fallacies, stance check, every quote verified | Not built | Your repo |
| Pitch accuracy | Own autocorrelation tracker | Praat via parselmouth | netmaxx |
| Uptalk robustness | Depends on punctuation | Questions detected from words | netmaxx |
| Conversation skills | Not covered (built for monologues) | Latency, interruptions, talk share, follow-ups, planted hooks, style matching | netmaxx |
| Main-message delivery | Not covered | 5-check emphasis score | netmaxx |
| Other delivery signals | Pace variation, monotone stretches, loudness emphasis, vocabulary variety, stumbles, time used | Trailing off, pace match to the persona | Both |
| Research behind thresholds | Sensible defaults, uncited | Cited per metric | netmaxx |
| Fairness | `clarity_pct` uses transcription confidence, which penalizes accents (your README already flags this) | Every voice metric relative to the speaker's own baseline | netmaxx |
| Tests | 20, including API request shapes and malformed Gemini output | 16 checks in one script | Your repo |

**Grades.** Your repo: A- as a debate grader; it does not cover conversations. netmaxx\_scoring: B+ as a conversation grader; C as a debate grader, since it has no argument analysis.

Your API settings check out against current docs: `gemini-3.8-flash` is the newest stable Flash on the [Gemini models page](https://ai.google.dev/gemini-api/docs/models), and `scribe_v2` with `no_verbatim` false keeps fillers and false starts per the [Scribe API reference](https://elevenlabs.io/docs/api-reference/speech-to-text/convert).

**The merged grader**, built inside your repo's `softskills-grader` package:

1. **Keep as-is:** Scribe settings, the Gemini extraction schema and quote verification, cache-by-hash, the rubric hash on every report, buckets plus deductions, letter grades, and the test style.
2. **Port in** `grader/conversation.py` with netmaxx's turn-taking and listening metrics, plus the main-message checks and trailing off, each registered in `CATALOG` so the YAML can use it.
3. **Pitch:** use Praat through parselmouth when installed; keep your tracker as the fallback.
4. **Uptalk:** decide statement versus question from the words, not the punctuation; when there is no punctuation, split sentences at pauses of 0.4 s or more.
5. **Fairness:** set `clarity_pct` weight to 0 by default and keep showing it as information.
6. **YAML additions:** a `curve:` option next to `buckets:` so small improvements still move the score (rematch deltas need this), and a `source:` field per metric so the card can cite the research.
7. **One rubric per mode:** `standards/debate.yaml` (your five factors, unchanged) and `standards/coffee_chat.yaml` (the six card stats).

One open calibration question: your hedge list counts "I think" and "in my opinion", mine does not. Count them separately and decide after the team's calibration recordings.

## Judged metrics: Gemini as a labeler, code as the scorer

Gemini never outputs the final number. It labels each of your sentences against a fixed rubric, quotes its evidence, and the code turns label counts into the CONTENT score, so the judgment that varies is small and every point is traceable.

Four guards make the judged part repeatable:

1. **Labels, not grades.** "Is this sentence the Situation, Task, Action or Result?" is far more stable than "rate this answer 1 to 10".
2. **Locked output.** Temperature 0 and a JSON response schema, so the shape never drifts.
3. **Verified evidence.** Every rubric item must quote the transcript. The code checks the quote appears verbatim (after lowercasing and stripping punctuation); a label with a fake quote is dropped and the call is retried once.
4. **Median of three.** Run the labeler 3 times in parallel and keep each sentence's majority label. It costs about 2 s more and removes most flip-flops.

| Mode | Gemini labels | Code computes CONTENT as |
| --- | --- | --- |
| Interview (STAR) | Each sentence: situation, task, action, result, or other; plus whether it contains a specific (number, name, outcome) | Per answer: 25 points for each STAR part present, plus up to 20 for specifics, capped at 100; averaged over answers |
| Coffee chat / alumni | Each of your questions: surface ("what do you do?"), personal (experience, opinion), or deep (reasons, values, feelings); each turn: self-disclosure yes or no | 50% question-depth mix (deep 1.0, personal 0.7, surface 0.3), 25% self-disclosure share, 25% main-point clarity |
| Argument | Your repo's schema: thesis, claims with support type, counterargument and rebuttal, fallacies, emotional appeals, stance | Your repo's logic and emotion factors, from verified quotes; ethos comes from the community (Debate Arena) |
| Rizz | Each turn: playful, specific compliment, callback to something they said, generic, or try-hard | Share of playful, specific and callback turns minus share of generic and try-hard |

```python
from pydantic import BaseModel
from typing import Literal

class SentenceLabel(BaseModel):
    i: int                                   # sentence index we sent
    label: Literal["situation", "task", "action", "result", "other"]
    has_specific: bool
    evidence: str                            # exact words from sentence i

class StarLabels(BaseModel):
    sentences: list[SentenceLabel]
    main_point: str                          # one sentence, used by the listener test

async def label_once(numbered_transcript: str) -> StarLabels:
    r = await gemini.aio.models.generate_content(
        model=JUDGE_MODEL,
        contents=STAR_RUBRIC + "\n\n" + numbered_transcript,
        config=types.GenerateContentConfig(
            temperature=0, response_mime_type="application/json",
            response_schema=StarLabels))
    return r.parsed

async def judge_content(sentences):
    runs = await asyncio.gather(*[label_once(number(sentences)) for _ in range(3)])
    labels = majority_vote(runs)                                   # per sentence
    labels = [l for l in labels if quote_in(l.evidence, sentences[l.i])]
    return star_score(labels)                                      # pure Python
```

The rubric prompt lists each label with a definition and two short examples, one that qualifies and one that does not. Gemini may also listen to the audio and write two or three plain-language coaching notes about tone ("you sped up when talking about your project"); those notes show on the card as text and never change a number.

## Live rapport meter: the chess eval bar

Rapport is a number from -100 to +100 shown as a vertical eval bar beside the call, and every turn gets a chess-style move badge. The persona's behavior is tied to the same number, so the bar is not decoration: when it drops, the persona actually gets colder.

**How it moves.** After each of your turns:

```latex
\Delta = \text{clamp}(\Delta_{\text{Gemini}}, -10, 10) + \text{nudges}, \qquad r_{t+1} = \text{clamp}(r_t + \Delta, -100, 100)
```

The Gemini part comes from a small judge call (temperature 0, JSON) that sees the persona profile, the hidden goal, the current rapport and the last exchange, and returns a delta plus a reason of 12 words or fewer. The nudges are deterministic and reuse the scoring engine's text functions:

| Signal in your turn | Nudge |
| --- | --- |
| Follow-up question on the persona's last turn | +4 |
| You bring up a planted hook (first time only) | +5 |
| You cut the persona off mid-sentence | -5 |
| Monologue: over 90 words in one turn | -3 |
| Two or more hedges in one turn | -1 |
| You make the ask before rapport reaches the threshold | -8, and the persona deflects |

**Move badges**, from the turn's total delta:

| Delta | Badge |
| --- | --- |
| +9 or more | Brilliant!! |
| +5 to +8 | Great! |
| +1 to +4 | Good |
| 0 | no badge |
| -1 to -3 | Inaccuracy?! |
| -4 to -7 | Mistake? |
| -8 or less | Blunder?? |

**Persona bands**, written into the persona's system prompt each turn:

| Rapport | Persona behavior |
| --- | --- |
| Below -40 (cold) | One-sentence answers, no personal details, hints at wrapping up |
| -40 to 20 (neutral) | Polite, answers what is asked, rarely asks back |
| 20 to 60 (warm) | Tells stories, asks you questions, drops the second planted hook |
| Above 60 (won over) | Offers help unprompted; says yes if you make the ask |

**Win and lose.** You win when rapport is above 60 and the judge marks that you made the ask (it returns `ask_made: true`). You lose if rapport hits -80 (the persona politely ends the call) or the 4-minute clock runs out.

**Why a one-turn lag.** The rapport judge runs in parallel with the persona's reply, so the new band applies from the next reply. That keeps reply latency flat, and it feels natural, since people also react a beat late.

```python
BADGES = [(9, "Brilliant!!"), (5, "Great!"), (1, "Good"), (0, None),
          (-3, "Inaccuracy?!"), (-7, "Mistake?"), (-100, "Blunder??")]

def badge(delta: int):
    return next(label for floor, label in BADGES if delta >= floor)

async def update_rapport(game, transcript):
    agent_last, user_last = last_exchange(transcript)
    j = await judge_rapport(game.scenario, game.rapport, agent_last, user_last)
    delta = max(-10, min(10, j.delta)) + nudges(game, agent_last, user_last, j.ask_made)
    game.rapport = max(-100, min(100, game.rapport + delta))
    game.turn_log.append({"t": now(), "rapport": game.rapport, "delta": delta,
                          "badge": badge(delta), "reason": j.reason})
    if j.ask_made and game.rapport > game.scenario.win_threshold:
        game.outcome = "won"
    await realtime.broadcast(f"session:{game.id}", "rapport", game.turn_log[-1])
```

The per-turn log doubles as the rapport line on the replay screen, so you can see exactly which sentence moved the bar.

## Game layer

The loop that makes this a game rather than a report card is win condition, live bar, card reveal, replay, rematch. Each piece below reuses data the pipeline already produces.

**Player card.** Six stats in a FIFA-style card: PACE, DELIVERY, CONFIDENCE, LISTENING, FLOW and CONTENT, with an overall rating that is the mode-weighted mean. Under each stat, one line names the biggest driver ("LISTENING 82: you followed up on Lisbon and remembered the hiring news"). The card renders as a shareable image for social posts.

**Ranks.** Your rank per mode is the average overall of your last 3 rounds, so one lucky round does not carry you.

| Rank | Rolling overall |
| --- | --- |
| Bronze | below 50 |
| Silver | 50 to 64 |
| Gold | 65 to 74 |
| Platinum | 75 to 84 |
| Diamond | 85 and up |

**XP.** 10 per round, 25 for a win, 5 per Brilliant!! move. XP unlocks new personas, not better scores.

**Replay timeline.** The recording plays under a strip of markers taken from the engine's event list (green for follow-ups, recalled hooks and a landed main point; red for fillers, rising endings, interruptions and long pauses) with the rapport line drawn above it. Clicking a marker jumps the audio there and shows a one-line tip for that event type, for example a filler shows "swap the um for a silent beat; a pause reads as confidence".

**Rematch.** Same persona, same hooks, same goal. The card then shows stat deltas against your last attempt ("+12 LISTENING"), which is the moment people screenshot.

**Daily challenge.** One constraint per day, each checked by an engine metric so it is pass or fail with no judgment call:

- Ask 3 follow-up questions (`follow_ups >= 3`)
- Zero question-like endings on statements (`rising_statement_share == 0`)
- Land your main point 5 out of 5 (`main_message.score == 100`)
- Keep talk share between 45% and 65% (`talk_share`)

## Modes as config

A mode is one YAML file: persona, goal, hooks and stat weights. Adding a scenario is writing a file, not code, so build one hero mode properly and add the others as files once the loop works.

Recommended set: **Alumni coffee chat** as the hero (clearest win condition, the referral), **Interview (STAR)** second, and **Rizz** or **Argument** as the crowd-pleaser for the demo.

```yaml
id: alumni_coffee_chat
title: "Coffee chat: the busy alumna"
mode: coffee_chat                 # selects the CONTENT labeler
time_limit_s: 240
persona:
  name: Dana Okafor
  role: Senior PM at a fintech, graduated 6 years before you
  voice_id: <elevenlabs voice id>
  backstory: >
    Friendly but stretched thin; agreed to 15 minutes as a favor.
    Values people who are specific and curious, dislikes being treated as a referral machine.
  opener: "Hey! Sorry, I'm a bit frazzled, I just got back from Lisbon on Sunday. What's up?"
rapport:
  initial: -10
  win_threshold: 60
  lose_threshold: -80
hidden_goal:
  description: Get Dana to offer a referral for the APM role
  ask_detector: The user explicitly asks for a referral, an intro, or to be put forward for a role
hooks:
  - id: lisbon_trip
    keywords: [lisbon, portugal, trip]
    planted_in: opener
  - id: team_hiring
    keywords: [hiring, headcount, team, growing]
    planted_in: warm_band            # only revealed once rapport is above 20
declared_main_point: true           # user types their one-line pitch before the call
weights: {PACE: 0.10, DELIVERY: 0.15, CONFIDENCE: 0.15, LISTENING: 0.30, FLOW: 0.15, CONTENT: 0.15}
```

An interview file would raise CONTENT and CONFIDENCE weights and set `mode: interview_star`; a rizz file would raise FLOW and LISTENING and set `mode: rizz`. The persona prompt template, rapport judge and scoring engine stay the same across every mode.

## Debate Arena: argue, get graded, get judged by the community

You record a 60-second argument on a motion and get the machine grade in about 15 seconds. The speech then goes into an arena where listeners judge it head to head against a speech from the other side, and the crowd's verdict sits next to the machine's on your card.

&#91;embedded content: debate pipeline · speaker, server and listener paths\]

The speaker path runs once per speech; the listener loop runs for every matchup, and the aggregator turns accumulated judgments into crowd scores beside the machine grade.

**Speaker side**

1. **Pick** a motion from a curated list and a side, yes or no.
2. **Record** a 60-second argument. Optional: an AI opponent (ElevenLabs voice, Gemini script with 3 tagged claims) argues the other side first, and your speech must answer it. Clash = share of the opponent's 3 claims your verified rebuttal quotes share content words with, the same rule as follow-up questions.
3. **Machine grade** with your repo's grader: pace, diction, tone, logic and emotion, plus a letter grade.
4. **Publish**, opt-in only: a consent screen, a Gemini safety check on the transcript, and an optional voice mask through ElevenLabs Voice Changer so listeners judge the argument rather than the voice. Test that the mask keeps your pitch movement before relying on it.

**Listener side**, about 2.5 minutes per matchup:

1. Pick a motion and state your stance on a 5-point scale before hearing anything.
2. Hear one speech from each side, in random order. The rating buttons unlock after 90% of each has played.
3. State your stance again.
4. Three quick picks: which had better reasoning (A or B), which was more moving (A or B), and would you trust each speaker to brief you on this (yes or no each).

**Why picks instead of 1-to-100 sliders.** Every listener uses a slider differently, and an average over a handful of votes swings wildly. Choosing the better of two is a judgment people make more consistently, and comparative judgment builds a full quality scale from such pairs ([adaptive comparative judgement](https://en.wikipedia.org/wiki/Adaptive_comparative_judgement)). One caution from that literature: choosing pairs adaptively can inflate the reliability statistic, even on random data ([Bramley & Wheadon 2015](https://www.cambridgeassessment.org.uk/Images/296241-the-reliability-of-adaptive-comparative-judgment.pdf)), so pair randomly and check accuracy by simulation instead.

**How votes become scores**

| Score | Question it answers | Computed as |
| --- | --- | --- |
| Logos | Who reasoned better? | Bradley-Terry strength from the reasoning picks, with a same-side bias term removed; percentile within the motion |
| Pathos | Who moved people? | The same, from the "more moving" picks |
| Persuasion | Who changed minds? | A listener who moved toward a side counts as a head-to-head win for that side's speech, the way [Intelligence Squared](https://en.wikipedia.org/wiki/Intelligence_Squared) awards debates to the side that changed the most minds; Bradley-Terry percentile |
| Ethos | Would people trust you on this? | Lower bound of the Wilson interval on the share of "yes, I'd trust them" votes, so 2 of 2 never outranks 95 of 100 ([Evan Miller](https://evanmiller.org/how-not-to-sort-by-average-rating.html)) |
| Debate Elo | Who wins matchups? | Per player, K = 32, from head-to-head persuasion results |

**Fairness controls, tested in simulation.** `arena_ratings.py` implements all of the above and passes 13 checks against a simulated community of 150 honest listeners and 20 random clickers judging 30 speeches with known true quality.

- **"I agree with you" bias.** The model estimates how much listeners favor their own side, using the stance they gave before listening, and removes it. With 70% of listeners leaning yes, uncorrected rankings put yes-side speeches 22.3 percentile points above no-side ones against a true gap of 11.7; corrected, 9.0.
- **Persuasion as wins, not averages.** Counting stance moves as head-to-head wins tracked true persuasiveness at rank correlation 0.94; the raw average stance shift managed 0.63, because it ignores how strong the opponent was.
- **Rater quality.** 4 gold matchups pair a deliberately strong speech with a deliberately weak one. A rater's weight is their accuracy on gold (floor 0.2), and raters with fewer than 3 gold answers count half. Random clickers averaged weight 0.52 against 0.88 for honest raters.
- **Hygiene.** No judging your own speech, one vote per rater per pair, the 90% listen gate, and 20 matchups per hour at most.
- **Enough votes.** Scores show "provisional" below 10 judgments and "settled" from 20. In simulation, rank correlation with the truth was 0.60 at 5 judgments per speech (worst run 0.28), 0.75 at 10, 0.84 at 20 and 0.89 at 40.
- **Judge XP.** Listeners earn XP when their pick matches the eventual consensus, so careful judging pays. This also rewards conformity, so cap it per day.

**The debate card** puts two columns side by side: the machine grade (logic as machine logos, emotion as machine pathos, plus pace, diction and tone) and the crowd scores (logos, pathos, ethos and persuasion percentiles, debate Elo). The gap is the coaching moment: "the grader rates your logic 88, the crowd puts your reasoning at the 61st percentile; listeners may not be hearing your evidence."

**Data and routes**

| Table | Key columns |
| --- | --- |
| `motions` | id, text, active |
| `speeches` | id, player\_id, motion\_id, side, audio\_path, masked\_audio\_path, machine\_report (jsonb), published, moderation\_status |
| `judgments` | id, rater\_id, speech\_a, speech\_b, pre\_stance, post\_stance, logos\_pick, pathos\_pick, trust\_a, trust\_b, listened\_a\_pct, listened\_b\_pct, created\_at |
| `gold_pairs` | speech\_a, speech\_b, expected\_pick |
| `arena_scores` | speech\_id, logos, pathos, ethos, persuasion, judgments, provisional, settled, aggregator\_version |
| `debate_elo` | player\_id, rating |

| Route | Purpose |
| --- | --- |
| `POST /api/speeches` | Upload a recording, run the machine grade |
| `POST /api/speeches/{id}/publish` | Consent, safety check, optional voice mask, then into the arena |
| `GET /api/arena/{motion}/next` | A random opposite-side pair the listener has not judged and did not record; a gold pair 1 time in 10, and in each new listener's first matchups |
| `POST /api/judgments` | Store one judgment; rejects it if either listen percentage is under 90 |
| `GET /api/speeches/{id}/arena` | Current crowd scores for the card |

A job runs `aggregate_motion` every 60 seconds for each motion with new judgments. It is a pure function, so the same votes always give the same scores, and it broadcasts updated scores to the speaker on their Realtime channel.

**In the schedule** the arena takes lane B's second-mode slot (hours 16 to 28). Your repo's grader already exists, so lane C spends hours 0 to 6 merging the two graders instead of building one.

**In the demo** put a QR code on screen during the pitch. People in the room judge two prepared speeches live, and the crowd scores move on the big screen. Seed each demo speech with about 20 team and attendee judgments beforehand, so the numbers are settled rather than provisional.

## Data model and API

Five Postgres tables in Supabase cover everything; audio goes to a private Storage bucket, and live updates ride a Realtime broadcast channel per session.

| Table | Key columns | Written by |
| --- | --- | --- |
| `scenarios` | id, mode, yaml (jsonb), active | Seeded from the YAML files |
| `sessions` | id, user\_id, scenario\_id, conversation\_id, status, outcome, rapport\_final, audio\_path, declared\_main\_point, started\_at | `POST /api/sessions`, `/bind`, `/finish` |
| `turns` | session\_id, idx, speaker, text, start\_s, end\_s, rapport\_after, delta, badge, reason | Live loop, one row per turn |
| `cards` | session\_id, overall, stats (jsonb), metrics (jsonb), events (jsonb), judged (jsonb), engine\_version | Post-call worker |
| `ranks` | user\_id, mode, rolling\_overall, tier, xp | Post-call worker |

Store `engine_version` and the calibration values on every card. When you retune a threshold, old cards still explain their own numbers.

| Route | Purpose |
| --- | --- |
| `GET /api/scenarios` | List playable scenarios |
| `POST /api/sessions` | Create a round; returns session id and WebRTC conversation token |
| `POST /api/sessions/{id}/bind` | Attach the ElevenLabs conversation id to the round |
| `WS /ws` | Speech Engine connection; `on_transcript` runs the live loop |
| `POST /api/sessions/{id}/finish` | Upload mic audio and persona intervals; starts the post-call worker |
| `GET /api/sessions/{id}/card` | Fetch the finished card, events and turn log |
| `GET /api/me/progress` | Ranks, XP, stat history for the profile page |

Realtime events on channel `session:{id}`: `rapport` (each turn), `ended` (win, lose or timeout), `results_ready` (card saved).

## Build schedule and team split

One person per lane, with the voice loop working end to end by hour 4 and the hero mode finished by hour 24, so the last 9 hours before the freeze go to calibration, a second mode and rehearsal.

&#91;embedded content: build schedule · 4 lanes, 5 milestones over 36 hours\]

If hour 4 slips, cut the second mode first, then daily challenges; never cut rehearsal or the backup video.

Hour-1 checklist, before anyone builds on assumptions:

- [ ] Speech Engine quickstart runs with Gemini in place of OpenAI
- [ ] Client event names for mode changes and messages confirmed, and the conversation id reachable from the browser
- [ ] A 30-second MediaRecorder clip goes through Scribe with word timestamps, with fillers kept
- [ ] `netmaxx_scoring.py` runs on that clip and returns a card
- [ ] Server deployed with a public `wss://` URL

## Demo script, risks and fallbacks

The 3-minute demo shows one live round with a deliberate mistake and recovery, then proves the score is not vibes.

1. **0:00 to 0:20, hook.** Soft skills decide referrals and offers, and nobody gets feedback on them. We made practice a game where every point is measured.
2. **0:20 to 1:50, live round with Dana.** Follow up on Lisbon (bar jumps, "Great!"), then make the ask too early on purpose ("Blunder??", Dana deflects), recover with a specific question, make the ask again, win.
3. **1:50 to 2:30, card reveal.** Walk the six stats, click the red rising-ending marker to hear it and read the tip, open the main-message panel with its 5 checks.
4. **2:30 to 3:00, credibility.** Run the scoring engine on the same audio twice on screen: identical card. Flash the science table. End on the rematch delta.

| Risk | Mitigation |
| --- | --- |
| Venue Wi-Fi or an API fails mid-demo | Pre-recorded video of the full round, plus a saved session whose card renders from stored JSON |
| Persona voice leaks into the mic, causing fake interruptions and garbage transcripts | Headphones always; `echoCancellation` on |
| Persona replies take over 2 s | Flash-tier model, `max_output_tokens` 160, persona prompt asks for 1 to 3 sentences, rapport judge runs in parallel |
| Speech Engine needs a public WebSocket URL | Deploy the server to a cloud host in the first 6 hours; keep ngrok as backup |
| Scribe drops filler words | Check in hour 1; fall back to a verbatim Gemini transcription pass for filler counts |
| Pitch tracker octave errors in a noisy room | Semitone values beyond 12 st are dropped; record in a quiet corner; test the mic before judging |
| Gemini labels flip between runs | Majority of 3 runs, evidence quotes verified against the transcript |
| Judges ask about bias | Every voice metric is relative to the speaker's own baseline; natural pitch, accent and loudness are never scored |
| Users' voice recordings | Private storage bucket, signed URLs, auto-delete after 7 days |

## Sources

Platform docs

- [ElevenLabs Speech Engine overview](https://elevenlabs.io/docs/overview/capabilities/speech-engine)
- [ElevenLabs Speech Engine quickstart](https://elevenlabs.io/docs/eleven-api/guides/cookbooks/speech-engine)
- [ElevenLabs Scribe speech-to-text](https://elevenlabs.io/blog/meet-scribe)
- [Gemini API models](https://ai.google.dev/models?hl=en)

Research

- [Huang et al. 2017, It doesn't hurt to ask: question-asking increases liking](https://dash.harvard.edu/handle/1/42704282)
- [Templeton et al. 2022, Fast response times signal social connection in conversation](https://pmc.ncbi.nlm.nih.gov/articles/PMC8794835)
- [Stivers et al. 2009, Universals and cultural variation in turn-taking](https://forms.mpi.nl/node/50939)
- [Hirschi, Wilson & Gilbert 2022, Speak up! Mistaken beliefs about how much to talk](https://dtg.sites.fas.harvard.edu/HIRSCHI%20WILSON%20GILBERT%202022.pdf)
- [Ireland et al. 2011, Language style matching predicts relationship initiation and stability](https://pubmed.ncbi.nlm.nih.gov/21149854/)
- [Smith et al. 1975, Effects of speech rate on personality perception](https://sonicfield.org/library/effects-of-speech-rate-on-personality-perception)
- [Brown, Strong & Rencher 1973, Perceptions of personality from speech](https://scholarsarchive.byu.edu/facpub/5973)
- [Benki et al. 2011, Effects of speech rate, pitch, and pausing on survey participation decisions](https://iab.de/publication/?id=1927064)
- [Schroeder & Epley 2015, The sound of intellect](https://www.psychologicalscience.org/journals/psychological-science/0956797615572906/)
- [Niebuhr 2020, What makes business speakers sound charismatic?](https://cadernos.abralin.org/index.php/cadernos/article/view/272)
- [Erickson et al. 1978, Powerful and powerless speech](https://scholars.duke.edu/publication/784538)
- [Klofstad, Anderson & Peters 2012, Voice pitch and leadership (Duke summary)](https://today.duke.edu/2012/03/andersonvote)
- [Klofstad & Anderson, Do lower voices pick better leaders?](https://sciencetrends.com/voice-pitch-influences-voters-but-do-we-pick-better-leaders-as-a-consequence)
- [Anderson et al. 2014, Vocal fry may undermine the success of young women in the labor market](https://pmc.ncbi.nlm.nih.gov/articles/PMC4037169)
- [Fraundorf, Watson & Benjamin 2010, Contrastive accenting and recognition memory](https://www.ideals.illinois.edu/items/14788)
- [MacGregor, Corley & Donaldson 2010, Silent pauses have consequences for listeners](https://www.stir.ac.uk/research/hub/publication/789589)

Debate Arena and grader comparison

- [WahoosHackNC2026 softskills-grader](https://github.com/alenabhilash03-beep/WahoosHackNC2026)
- [ElevenLabs speech-to-text API reference](https://elevenlabs.io/docs/api-reference/speech-to-text/convert)
- [Gemini API models](https://ai.google.dev/gemini-api/docs/models)
- [Adaptive comparative judgement](https://en.wikipedia.org/wiki/Adaptive_comparative_judgement)
- [Bramley & Wheadon 2015, The reliability of adaptive comparative judgment](https://www.cambridgeassessment.org.uk/Images/296241-the-reliability-of-adaptive-comparative-judgment.pdf)
- [Intelligence Squared](https://en.wikipedia.org/wiki/Intelligence_Squared)
- [Evan Miller, How not to sort by average rating](https://evanmiller.org/how-not-to-sort-by-average-rating.html)
