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

from .http import post_with_retry

URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
PROMPT_VERSION = "2026-10-10.1"   # bump when the prompt or schema changes (invalidates cache)

SUPPORT_TYPES = ["none", "reasoning", "example", "statistic", "authority", "analogy"]
REASONING_FALLACIES = ["straw_man", "false_dilemma", "slippery_slope", "hasty_generalization",
                       "circular_reasoning", "bandwagon", "other"]
MANIPULATIVE_FALLACIES = ["appeal_to_fear", "ad_hominem"]
APPEAL_TYPES = ["personal_story", "vivid_imagery", "shared_values", "human_stakes",
                "rhetorical_question", "call_to_action"]

_QUOTE = "Copied word for word from the transcript. Empty string if there is none."

SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {
            "type": "string", "enum": ["yes", "no", "unclear"],
            "description": "Which side of the topic question the speech argues for.",
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
                 "counterargument_quote", "rebuttal_quote", "fallacies", "emotional_appeals"],
}

SYSTEM = """You annotate transcripts of short spoken arguments. You extract structure; you do not grade.

Rules:
- Every quote must be copied word for word from the transcript, 4 to 30 words long. Never paraphrase, never fix grammar, never join separate passages. Quotes that are not in the transcript are discarded.
- If something is absent, return an empty string or an empty list. Do not stretch to fill a field.
- Do not judge whether claims are true. Record only whether the speaker backed them up.
- Do not give scores, ratings or opinions on quality.
- List each claim once. A claim and its support must be different passages.
- Only flag a fallacy when it is clear-cut."""


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


def extract(transcript: str, topic: str, cfg: dict, cache_dir: str) -> dict:
    request = build_request(transcript, topic, cfg)
    key = hashlib.sha256(json.dumps([PROMPT_VERSION, request], sort_keys=True).encode()).hexdigest()[:32]
    cache_file = Path(cache_dir) / "extractions" / f"{key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    # Try the configured model first. If it stays overloaded, try each fallback model.
    # Whichever model answers, the result is cached under the same key, so this
    # recording keeps the same annotations from then on.
    models = [request["model"]] + [m for m in cfg.get("fallback_models") or [] if m != request["model"]]
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    problems = []
    for model in models:
        response = post_with_retry(URL, headers=headers, json={**request, "model": model}, timeout=180)
        if response.status_code < 400:
            break
        problems.append(f"{model}: {response.status_code} {response.text[:300]}")
    else:
        raise RuntimeError("Gemini could not analyse the transcript.\n" + "\n".join(problems))
    result = parse_response(response.json())

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result))
    return result
