# Spoken-argument grader

Grades a one-minute spoken argument on pace, diction, tone, logic and appeal to
emotion. ElevenLabs and Gemini supply raw material; **our code measures, checks
and scores it against a rubric you edit in `config/standards.yaml`.**

## Run it

```bash
pip install -r requirements.txt      # numpy, scipy, pyyaml, requests
# ffmpeg decodes browser recordings (webm, m4a, mp3 ...). Install it with
#   conda install -c conda-forge ffmpeg    or    brew install ffmpeg
# Without it, WAV files work everywhere and m4a/mp3 work on a Mac.

python demo.py                       # no API keys needed: synthetic recording + canned API responses
python tests/test_pipeline.py        # 22 checks, no network

cp .env.example .env                 # then add ELEVENLABS_API_KEY and GEMINI_API_KEY
python grade.py speech.webm --topic "Should the frontier of AI be paced?" --side yes --out report.json
```

From your backend it is one call:

```python
from grader import grade
report = grade("uploads/abc.webm", topic="Should the frontier of AI be paced?", side="yes")
report["overall"]            # {"score": 84.6, "letter": "B"}
report["factors"]["logic"]   # sub-grade, every metric, its bucket, every deduction
report["evidence"]           # quotes, pauses, fillers ... for the feedback screen
```

`sample_report.json` (written by `demo.py`) shows the full shape to build a UI against.

## The pipeline

| # | Step | Who does it | Output |
|---|------|-------------|--------|
| 1 | Transcribe | **ElevenLabs Scribe** (`grader/transcribe.py`) | every word with start time, end time, confidence; fillers kept |
| 2 | Listen | **our code** (`grader/audio.py`) | pitch and loudness track from the raw audio |
| 3 | Annotate | **Gemini** (`grader/extract.py`) | thesis, claims, support, counterargument, fallacies, emotional appeals, each as a verbatim quote |
| 4 | Measure | **our code** (`grader/features.py`) | discards any Gemini item whose quote is not in the transcript, then counts and measures 28 numbers |
| 5 | Score | **our code** (`grader/scoring.py`) | buckets, deductions and weights from `standards.yaml` -> five sub-grades and a main grade |

Neither model ever produces a score. Gemini cannot raise a grade by inventing
material: unverifiable quotes are dropped and listed in
`report["evidence"]["argument"]["discarded_from_gemini"]`.

## Why the grade is repeatable

- Steps 2, 4 and 5 are plain code: same inputs, same numbers.
- Steps 1 and 3 run once per recording and are saved in `cache/` (keyed by the
  audio's SHA-256, and by transcript + topic + model + prompt version). Grading
  the same recording again never calls an API and returns the identical report.
- Change the rubric and old recordings re-grade from the cache for free. Every
  report carries `standards_id`, a hash of the rubric that produced it.
- Both APIs are also sent a fixed seed. Their docs call seeded output best-effort,
  so the cache, not the seed, is the guarantee. Two *different* recordings of the
  same words are separate inputs and may differ slightly.

## Editing the standards

Everything is in `config/standards.yaml`; the header explains the format.

```yaml
words_per_minute:
  weight: 3
  buckets:
    - {from: 140, to: 195, score: 100}   # from is inclusive, to is exclusive
    - {from: 130, to: 140, score: 85}
  else: 40                               # anything that matches no bucket
deductions:
  - {metric: long_pause_count, free: 1, points_each: 5, max_points: 20}
```

- Factor score = weighted average of its metric scores, minus deductions, clamped to 0-100.
- Overall = weighted average of factor scores. Weights are relative, so deleting a
  factor or metric re-balances the rest.
- `python grade.py --list-metrics` lists every metric you can bucket or deduct on.
- Word lists (fillers, hedges, connectives, emotion words) are in `config/lexicons.yaml`.
- Overlapping buckets, unknown metric names and bad numbers are rejected at load time.
- To add a new metric: compute it in `features.py`, add it to `CATALOG`, use it in the YAML.

## Research basis for the standards

The bucket edges in `config/standards.yaml` are taken from published studies
wherever one exists. This section says which number comes from which study, how
strong that evidence is, and what is still a judgment call.

### What research can and cannot settle

Studies report two useful things: **norms** (what fluent speakers typically do) and
**effects** (what makes listeners rate a speaker as more confident or persuasive).
No study says "150 words per minute is an A". So the edges of each bucket are
sourced, and one fixed ladder turns them into scores, the same way for every metric:

| Score | Meaning |
|---|---|
| 100 | Inside the range a study links to the best listener outcome, or inside the published range for fluent speakers |
| 85 | Normal for fluent speakers; no study shows harm |
| 65 | Outside the published normal range, or on the wrong side of a published threshold |
| 40 | A range where a study measured a negative effect on listeners, or outside everything the studies observed |
| 0 | The element is missing altogether (used once, for zero supported arguments) |

Metric weights follow the strength of the evidence, not our preference:

| Tier | Weight | Evidence |
|---|---|---|
| A | 3 | An experiment or meta-analysis ties the metric to listener judgments and gives a number in our units |
| B | 2 | A published norm in our units, or a proven effect with no published threshold. Never scores below 65, because no study shows harm |
| C | 1 | No usable published number. Placeholder until calibrated on our own recordings |
| Rule | n/a | A rule of the game, not a research finding |

### Pace

| Metric | Tier | Standard | Evidence |
|---|---|---|---|
| Words per minute | A | 140-195 = 100; 130-140 and 195-205 = 85; 113-130 and 205-218 = 65; under 113 or over 218 = 40 | Guyer et al. (2025), six experiments, N = 3,958: listeners rated speaker confidence 2.95 of 7 at 113 wpm, 4.70 at 148, 5.89 at 192 and 5.39 at 197. Attention to argument quality was strongest at 148 and 192 wpm and significantly lower at 202. The study treats 113-218 wpm as the natural range. Norms agree: British lectures average 173 wpm (Wang 2021); mixed genres averaged 170 wpm in 1990 (Tauroza & Allison, as cited by Wang). |
| Time in pauses | B | 15-30% = 100; 10-15% and 30-40% = 85; otherwise 65 | Native English speakers spent 21% of response time in silent pauses (Wang 2008). Real university lectures work out to about 26% (our calculation from Nishizawa 2024). |
| Allotted time used | Rule | 90%+ = 100 | Game rule. |
| Long pauses (deduction) | B | A pause over 2 s; first one free | Pauses over 2 s were 2.8% of pauses in spontaneous speech and 0% in read speech (Campione & Véronis 2002). Lecturers make about 21 silent pauses a minute (Nishizawa 2024), so one such pause in a one-minute speech is within normal. |

A silence counts as a pause from 0.25 s, the cut-off recommended by De Jong & Bosker (2013).

### Diction

| Metric | Tier | Standard | Evidence |
|---|---|---|---|
| Fillers per 100 words | A | under 2.0 = 100; 2.0-2.9 = 85; 2.9-7.9 = 65; 7.9+ = 40 | In 192,000 words of conversation from 96 speakers, people averaged 2.56 fillers per 100 words, with group means from 2.04 to 2.88 (Bortfeld et al. 2001). Lecturers work out to about 2.6 (our calculation from Nishizawa 2024). At 7.94 per 100 words, listeners rated speakers less certain and less prepared, and were less willing to consult them again (Lee & Papafragou 2026). |
| Repeated words (deduction) | B | First two free | Speakers repeat 1.47 words per 100 (Bortfeld et al. 2001), about two in a 150-word speech. |
| Vocabulary variety | C | Placeholder | No adult norm fits our measure. The adult study we found uses a 5-word window (Cunningham & Haley 2020); ours uses 40. |
| Word clarity | C | Placeholder | Confidence values are specific to the transcription engine. No published norm can exist. |

### Tone

| Metric | Tier | Standard | Evidence |
|---|---|---|---|
| Pitch variation (PVQ) | A | 20-34% = 100; 16-20% = 85; 11-16% = 65; under 11% = 40; over 34% = 65 | Hincks (2005) defined the pitch variation quotient: the standard deviation of pitch divided by its mean, per 10 s of speech. It correlated r = .83 with listeners' ratings of liveliness. Speakers averaging below .16 were rated more monotone than lively. Speaker averages ran from .11 to .24, and single segments from .06 to .34. We now compute exactly this measure. |
| Hedges per 100 words | A | under 0.6 = 100; 0.6-1.9 = 85; 1.9+ = 40 | Six colloquial hedges in a roughly 300-word argument (about 1.9 per 100) lowered ratings of the speaker and of the argument. Professional hedges such as "probably" and "may" did not (Durik et al. 2008). Our hedge list now contains colloquial hedges only. |
| Uptalk | B | under 10% = 100; 10-37% = 85; 37%+ = 65 | Falling intonation at the end of sentences signals more confidence than rising intonation, across three experiments (Vaughan-Johnston et al. 2024). The only rate we found is from a TV quiz show, where contestants used uptalk on 37% of responses (Linneman 2013). That setting differs from a speech, so this edge is a weak anchor. |

### Logic

| Metric | Tier | Standard | Evidence |
|---|---|---|---|
| Supported arguments | A | 3+ = 100; 2 = 85; 1 = 65; 0 = 0 | Spelling out the support for a claim makes a message more persuasive: r = .116 across 27 studies, N = 5,808. Citing sources: r = .064 across 23 studies, N = 5,358 (O'Keefe 1998). Three claims was the most persuasive number when the audience knows it is being persuaded (Shu & Carlson 2014). |
| Position stated | A | stated and restated = 100; stated once = 85; never stated = 40 | Messages that state their conclusion outright are more persuasive than those that leave it implied: r = .123 across 14 studies, N = 2,649 (O'Keefe 1997). |
| Other side addressed | A | raised and rebutted = 100; not raised = 85; raised but not rebutted = 65 | Against a one-sided message, raising and rebutting the other side is more persuasive (r = +.077, 42 studies), but raising it without rebutting is less persuasive (r = -.049, 65 studies) (O'Keefe 1999). |
| Reasoning connectives | C | Placeholder | No published rate for our word list. |
| Fallacies, unsupported claims, wrong side | Rule | Fixed point deductions | Team decisions. |

### Appeal to emotion

| Metric | Tier | Standard | Evidence |
|---|---|---|---|
| Emotional appeals | B | 3+ = 100; 1-2 = 85; 0 = 65 | Stories, metaphors, rhetorical questions, moral conviction and voicing the group's feelings are among twelve tactics that raised executives' leadership ratings by about 60% after training (Antonakis et al. 2012). No study gives a required number. |
| Audience address (we / you) | B | 4.9+ = 100; 2.4-4.9 = 85; under 2.4 = 65 | In 2.6 million words of natural speech, "we" words are 0.87% and "you" words 4.04% of all words (4.91 together). Across all genres the means are 0.72% and 1.70% (2.42 together) (Pennebaker et al. 2015). |
| Emotion words | C | Placeholder | The published norm (6.54% in natural speech) uses the LIWC dictionary. Our word list is far shorter, so the norm does not transfer. |
| Fear-mongering, personal attacks | Rule | Fixed point deduction | Team decision. |

### What changed because of the research

- **Raising the other side without answering it now scores below not raising it.** We had it the other way round; the meta-analysis says otherwise.
- **Loudness variation was removed.** In 51 speeches rated by 50 listeners, variation in loudness had almost no relation to perceived charisma: r = .05 for women, -.09 for men (Niebuhr & Skarnitzl 2021).
- **Pace variation and variety of emotional appeals were removed.** We found no study to anchor them.
- **The ideal pace band widened** from 140-170 to 140-195 wpm.
- **Pitch variation is measured the way the cited study measures it**, so its thresholds apply directly.
- **"Probably", "perhaps" and "might" no longer count as hedges.**
- **A pause counts from 0.25 s** (was 0.5 s) **and is "long" from 2 s** (was 1.5 s).

### What is still a judgment call

- **The five factor weights** (20/20/20/25/15). No study ranks pace against logic.
- **The ladder values** 100/85/65/40. The edges are sourced; the scores attached to them are a convention.
- **Deduction sizes** (5 points per long pause, 8 per fallacy, and so on).
- **Edges between tested points.** Guyer tested 113, 148, 192, 197 and 202 wpm, so 130 and 140 are interpolated. The lower edge of the top filler band (2.0) and the top appeal count (3) are our choices.
- **The tier C placeholders.**

### Limits of the evidence

- The studies used their own speakers, tasks and tools. Hincks studied student presentations; Bortfeld studied conversation; Durik studied written arguments; Guyer changed the speed of recordings digitally. None studied one-minute debate speeches.
- Effect sizes in persuasion research are small (r around .05 to .12). They justify the direction of a standard more than its exact size.
- We read these only as an abstract or a secondary summary, not the full paper: Vaughan-Johnston et al. 2024, Linneman 2013, Shu & Carlson 2014, and the 1990 Tauroza & Allison averages. Antonakis et al. is the authors' own magazine summary of their study.
- Lee & Papafragou 2026 is a conference paper using 50-word clips.
- Two figures are our own arithmetic on published means and are marked as such above.
- To calibrate properly, collect 30 or more of your own recordings with human ratings and compare.

### Sources

1. Antonakis, J., Fenley, M., & Liechti, S. (2012). Learning charisma. *Harvard Business Review*, June 2012. https://hbr.org/2012/06/learning-charisma-2
2. Bortfeld, H., Leon, S. D., Bloom, J. E., Schober, M. F., & Brennan, S. E. (2001). Disfluency rates in conversation: Effects of age, relationship, topic, role, and gender. *Language and Speech, 44*(2), 123-147. https://psychology.psy.sunysb.edu/sbrennan-/papers/bortetal.pdf
3. Campione, E., & Véronis, J. (2002). A large-scale multilingual study of silent pause duration. *Speech Prosody 2002*. https://www.isca-archive.org/speechprosody_2002/campione02_speechprosody.pdf
4. Cunningham, K. T., & Haley, K. L. (2020). Measuring lexical diversity for discourse analysis in aphasia: Moving-average type-token ratio and word information measure. https://talkbank.org/aphasia/publications/2020/Cunningham20.pdf
5. De Jong, N. H., & Bosker, H. R. (2013). Choosing a threshold for silent pauses to measure second language fluency. *DiSS 2013*. https://www.isca-archive.org/diss_2013/jong13_diss.pdf
6. Durik, A. M., Britt, M. A., Reynolds, R., & Storey, J. (2008). The effects of hedges in persuasive arguments: A nuanced analysis of language. *Journal of Language and Social Psychology, 27*(3), 217-234. https://doi.org/10.1177/0261927X08317947
7. Guyer, J. J., Vaughan-Johnston, T. I., Fabrigar, L. R., et al. (2025). Vocal speed and processing of persuasive messages: Curvilinear processing effects. *Journal of Nonverbal Behavior, 49*, 171-203. https://doi.org/10.1007/s10919-024-00477-6
8. Hincks, R. (2005). Measuring liveliness in presentation speech. *Interspeech 2005*. https://www.isca-archive.org/interspeech_2005/hincks05_interspeech.pdf
9. Lee, J. H. N., & Papafragou, A. (2026). Disfluency in spontaneous speech: Social attribution and behavioral consequences. *Proceedings of the Annual Meeting of the Cognitive Science Society, 48*. https://escholarship.org/content/qt8579j2fh/qt8579j2fh.pdf
10. Linneman, T. J. (2013). Gender in Jeopardy!: Intonation variation on a television game show. *Gender & Society, 27*(1), 82-105. https://philpapers.org/rec/LINGIJ
11. Niebuhr, O., & Skarnitzl, R. (2021). Those who shout the loudest: Do they sound more charismatic? *1st International Conference on Tone and Intonation*. https://doi.org/10.21437/TAI.2021-1
12. Nishizawa, H. (2024). Authenticity of academic lecture passages in high-stakes tests: A temporal fluency perspective. *Language Testing*. https://doi.org/10.1177/02655322241262453
13. O'Keefe, D. J. (1997). Standpoint explicitness and persuasive effect: A meta-analytic review of the effects of varying conclusion articulation in persuasive messages. *Argumentation and Advocacy, 34*(1), 1-12. https://dokeefe.net/pub/OKeefe97AA.pdf
14. O'Keefe, D. J. (1998). Justification explicitness and persuasive effect: A meta-analytic review of the effects of varying support articulation in persuasive messages. *Argumentation and Advocacy, 35*(2), 61-75. https://dokeefe.net/pub/OKeefe98AA.pdf
15. O'Keefe, D. J. (1999). How to handle opposing arguments in persuasive messages: A meta-analytic review of the effects of one-sided and two-sided messages. *Annals of the International Communication Association, 22*(1), 209-249. https://dokeefe.net/pub/OKeefe99AICA.pdf
16. Pennebaker, J. W., Boyd, R. L., Jordan, K., & Blackburn, K. (2015). *The development and psychometric properties of LIWC2015*. University of Texas at Austin. https://doi.org/10.15781/T29G6Z
17. Shu, S. B., & Carlson, K. A. (2014). When three charms but four alarms: Identifying the optimal number of claims in persuasion settings. *Journal of Marketing*. https://doi.org/10.1509/jm.11.0504
18. Vaughan-Johnston, T. I., Guyer, J. J., Fabrigar, L. R., Lamprinakos, G., & Briñol, P. (2024). Falling vocal intonation signals speaker confidence and conditionally boosts persuasion. *Personality and Social Psychology Bulletin*. https://doi.org/10.1177/01461672241262180
19. Wang, B. (2008). What can we tell from these temporal measures? Temporal measures as indices of oral proficiency. *English Language Teaching, 1*(2), 21-31. https://files.eric.ed.gov/fulltext/EJ1082849.pdf
20. Wang, L. (2021). British English-speaking speed 2020. *Academic Journal of Humanities & Social Sciences, 4*(5), 93-100. https://doi.org/10.25236/AJHSS.2021.040517

## Known limits

- The bucket numbers come from published studies where a study exists (see
  "Research basis for the standards"). Those studies used other speakers, tasks and
  measuring tools, so check the numbers against your own recordings before trusting
  fine distinctions. Four metrics still have placeholder numbers (tier C).
- The live ElevenLabs and Gemini calls follow the current API docs and are tested
  against canned responses in the documented shape. They have not been run against
  the real services from this build environment.
- The pitch tracker is verified against a synthetic signal with a known contour and
  gives plausible values on synthesised speech. It has not been checked on real
  microphone recordings; background noise will affect it.
- `clarity_pct` is the transcriber's confidence, so a strong accent or a poor
  microphone lowers it. Reduce its weight or remove it if that is unfair for your players.
- Tone measures how the voice sounds (inflection, uptalk, hedging), not
  how the speaker feels.
- Logic records whether claims were backed up, not whether they are true.
- `cache/` holds transcripts of player speech. Treat it as user data.
