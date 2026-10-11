"""Gemini: transcript -> the STRUCTURE of the argument, as JSON. Never a score.

Gemini answers questions of the form "which sentence is the thesis?" and "is
this claim backed by anything?". Every item must carry a verbatim quote, and our
code (features.py) throws away any item whose quote is not in the transcript.
Counting and grading happen in our code against config/standards.yaml.

Results are cached by transcript + topic + model + prompt version, so one speech
is only ever analysed once.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .http import RETRY_SAME_MODEL, first_success, post_with_retry

URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
PROMPT_VERSION = "2026-10-11.1"   # bump when the prompt or schema changes (invalidates cache)
HEDGE_AFTER = 4                   # seconds; a normal answer takes about 3, so race a backup soon after

SUPPORT_TYPES = ["none", "reasoning", "example", "statistic", "authority", "analogy"]
REASONING_FALLACIES = ["straw_man", "false_dilemma", "slippery_slope", "hasty_generalization",
                       "circular_reasoning", "bandwagon", "other"]
MANIPULATIVE_FALLACIES = ["appeal_to_fear", "ad_hominem"]
APPEAL_TYPES = ["personal_story", "vivid_imagery", "shared_values", "human_stakes",
                "rhetorical_question", "call_to_action"]

_QUOTE = "Copied word for word from the transcript. Empty string if there is none."

TOPIC_LEVELS = ["yes", "partly", "no"]

SCHEMA = {
    "type": "object",
    "properties": {
        "on_topic": {
            "type": "string", "enum": TOPIC_LEVELS,
            "description": ("Does the speech answer the topic question? 'yes' if it argues about the "
                            "question asked; 'partly' if much of it drifts to other subjects; 'no' if it "
                            "is about something else, or never engages with the question at all."),
        },
        "position": {
            "type": "string",
            "description": "One short sentence in your own words: what does the speaker want to happen?",
        },
        "stance": {
            "type": "string", "enum": ["yes", "no", "unclear"],
            "description": ("The speaker's answer to the topic question exactly as it is worded. Read "
                            "the question literally: for 'Should homework be banned?', a speaker who "
                            "wants to keep homework answers 'no'. 'unclear' if no answer can be told."),
        },
        "thesis_quote": {"type": "string", "description": "The sentence stating the speaker's position. " + _QUOTE},
        "conclusion_quote": {"type": "string", "description": "A closing sentence that restates or wraps up the position. " + _QUOTE},
        "arguments": {
            "type": "array",
            "description": "Each distinct claim the speaker makes in favour of their own stance.",
            "items": {
                "type": "object",
                "properties": {
                    "claim_quote": {"type": "string", "description": "The claim. " + _QUOTE},
                    "support_type": {"type": "string", "enum": SUPPORT_TYPES,
                                     "description": "What backs the claim. 'none' if it is only asserted."},
                    "support_quote": {"type": "string", "description": "The words that back the claim. " + _QUOTE},
                    "supports_stance": {"type": "boolean",
                                        "description": "True if this claim is a reason for the speaker's stance."},
                },
                "required": ["claim_quote", "support_type", "support_quote", "supports_stance"],
            },
        },
        "counterargument_quote": {"type": "string", "description": "Where the speaker states the other side's point. " + _QUOTE},
        "rebuttal_quote": {"type": "string", "description": "Where the speaker answers that point. " + _QUOTE},
        "fallacies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": REASONING_FALLACIES + MANIPULATIVE_FALLACIES},
                    "quote": {"type": "string", "description": _QUOTE},
                },
                "required": ["type", "quote"],
            },
        },
        "emotional_appeals": {
            "type": "array",
            "description": "Moments aimed at the listener's feelings or values.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": APPEAL_TYPES},
                    "quote": {"type": "string", "description": _QUOTE},
                },
                "required": ["type", "quote"],
            },
        },
    },
    "required": ["stance", "thesis_quote", "conclusion_quote", "arguments",
                 "counterargument_quote", "rebuttal_quote", "fallacies", "emotional_appeals", "on_topic", "position"],
}

SYSTEM = """You annotate transcripts of short spoken arguments. You extract structure; you do not grade.

Rules:
- Every quote must be copied word for word from the transcript, 4 to 30 words long. Never paraphrase, never fix grammar, never join separate passages. Quotes that are not in the transcript are discarded.
- If something is absent, return an empty string or an empty list. Do not stretch to fill a field.
- Do not judge whether claims are true. Record only whether the speaker backed them up.
- Do not give scores, ratings or opinions on quality.
- List each claim once. A claim and its support must be different passages.
- Only flag a fallacy when it is clear-cut.
- Judge on_topic by whether the speech engages with the topic question, not by how well it argues.
- For stance, first state the speaker's position, then decide whether that position answers the topic question as literally worded with yes or with no."""


def build_prompt(transcript: str, topic: str) -> str:
    return (f"Topic question: {topic}\n\n"
            f"Transcript of the speech (speech-to-text, may contain filler words):\n"
            f"<transcript>\n{transcript}\n</transcript>\n\n"
            f"Annotate the transcript according to the schema.")


def build_request(transcript: str, topic: str, cfg: dict) -> dict:
    generation = {"seed": cfg.get("seed", 7), "thinking_level": cfg.get("thinking_level", "low")}
    if cfg.get("temperature") is not None:      # only for models that still accept it
        generation["temperature"] = cfg["temperature"]
    return {
        "model": cfg.get("model", "gemini-3.8-flash"),
        "system_instruction": SYSTEM,
        "input": build_prompt(transcript, topic),
        "generation_config": generation,
        "response_format": {"type": "text", "mime_type": "application/json", "schema": SCHEMA},
        "store": False,
    }


def parse_response(payload: dict) -> dict:
    """Pull the JSON text out of an Interaction resource."""
    if isinstance(payload.get("output_text"), str):
        return json.loads(payload["output_text"])
    texts = [part.get("text", "")
             for step in payload.get("steps", []) if step.get("type") == "model_output"
             for part in step.get("content", []) if part.get("type") == "text"]
    if not texts:
        raise RuntimeError(f"Gemini response has no text output: {str(payload)[:400]}")
    return json.loads("".join(texts))


def api_keys() -> list[str]:
    """GEMINI_API_KEY, then any extra keys in GEMINI_API_KEYS (comma separated).

    Quota is counted per Google Cloud project, so extra keys only add capacity
    when each comes from a different project.
    """
    raw = f"{os.environ.get('GEMINI_API_KEY', '')},{os.environ.get('GEMINI_API_KEYS', '')}"
    return list(dict.fromkeys(key.strip() for key in raw.split(",") if key.strip()))


def key_attempts(models: list[str], post) -> list[tuple]:
    """Attempts for first_success: every key on the first model, then on the next model.

    post(model, headers, over) sends one request. Labels number the keys; they never show them.
    """
    keys = api_keys()
    return [(model if len(keys) == 1 else f"{model}, key {number}",
             lambda over, model=model, key=key: post(model, {"x-goog-api-key": key, "Content-Type": "application/json"}, over))
            for model in models for number, key in enumerate(keys, 1)]


def extract(transcript: str, topic: str, cfg: dict, cache_dir: str) -> dict:
    request = build_request(transcript, topic, cfg)
    key = hashlib.sha256(json.dumps([PROMPT_VERSION, request], sort_keys=True).encode()).hexdigest()[:32]
    cache_file = Path(cache_dir) / "extractions" / f"{key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    if not api_keys():
        raise RuntimeError("GEMINI_API_KEY is not set")
    # Try the configured model first. If it is rate limited, stays overloaded, or is
    # much slower than usual, try the other keys and then the fallback models, and keep
    # the first good answer. A rate limit is per key and model, so it moves on at once
    # instead of waiting. Whichever attempt answers, the result is cached under the same
    # key, so this recording keeps the same annotations from then on.
    models = [request["model"]] + [m for m in cfg.get("fallback_models") or [] if m != request["model"]]
    attempts = key_attempts(models, lambda model, headers, over: post_with_retry(
        URL, retry_on=RETRY_SAME_MODEL, cancelled=over, headers=headers, json={**request, "model": model}))
    try:
        response, _ = first_success(attempts, hedge_after=HEDGE_AFTER)
    except RuntimeError as error:
        raise RuntimeError(f"Gemini could not analyse the transcript.\n{error}") from None
    result = parse_response(response.json())

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result))
    return result
