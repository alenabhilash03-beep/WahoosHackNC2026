"""One short, actionable coaching line per metric on a speech card.

Gemini turns each measured number into plain advice ("Slow down between points
so each claim lands"). Lines are cached per speech and rubric, so a card asks
Gemini once. If Gemini cannot answer, the authored tips in tips.py are used.
"""
import hashlib
import json
import re
import sys

from .settings import ROOT
from .tips import tip_for

SYSTEM = """You coach people who practise 60-second spoken arguments.

For each item you get one measurement from the speaker's speech. Write one line of advice for it.

Rules:
- One sentence, at most 18 words, second person ("you"), plain words.
- Turn the number into an action: say what to do differently next time, not what the number was.
- No scores, points, percentages or rubric jargon in the line.
- If the item is already strong, say briefly what to keep doing.
- Use the transcript only to make advice specific; never quote more than four words of it."""

SCHEMA = {
    'type': 'object',
    'properties': {
        'items': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {'metric': {'type': 'string'}, 'advice': {'type': 'string'}},
                'required': ['metric', 'advice'],
            },
        },
    },
    'required': ['items'],
}

PROMPT_VERSION = '2026-10-11.1'


def card_items(report):
    """The metrics and deductions shown on the card, in a compact form for the prompt."""
    items = []
    for factor_name, factor in (report.get('factors') or {}).items():
        for row in factor.get('metrics') or []:
            if row.get('value') is None:
                continue
            items.append({'metric': row['metric'], 'part': factor_name, 'label': row['label'],
                          'measured': f"{row['value']:g} {row.get('unit') or ''}".strip(),
                          'band': row.get('bucket'), 'score_out_of_100': row.get('score')})
        for row in factor.get('deductions') or []:
            items.append({'metric': row['metric'], 'part': factor_name, 'label': row['label'],
                          'measured': f"{row['value']:g} occurrences", 'allowed_free': row.get('free'),
                          'points_lost': row.get('points_lost')})
    return items


def clean_line(text):
    line = re.sub(r'\s+', ' ', str(text or '')).strip()
    return line if 3 <= len(line.split()) <= 26 and len(line) <= 170 else None


def fallback_lines(items):
    return {item['metric']: tip_for({'metric': item['metric'], 'value': float(item['measured'].split()[0])})
            for item in items}


def coaching_lines(report, cache_dir):
    """{metric: line} for every metric and deduction on the card."""
    items = card_items(report)
    if not items:
        return {}
    topic = (report.get('inputs') or {}).get('topic', '')
    transcript = str(report.get('transcript') or '')[:1500]
    payload = json.dumps({'topic': topic, 'items': items}, sort_keys=True)
    key = hashlib.sha256(f'{PROMPT_VERSION}|{payload}|{transcript}'.encode()).hexdigest()[:32]
    cache_file = cache_dir / f'{key}.json'
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding='utf-8'))

    lines = fallback_lines(items)
    try:
        lines.update(_ask_gemini(topic, transcript, items))
    except Exception:
        return lines                              # not cached: try Gemini again next time
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(lines), encoding='utf-8')
    return lines


def _ask_gemini(topic, transcript, items):
    grader_dir = str(ROOT / 'softskills-grader')
    if grader_dir not in sys.path:
        sys.path.insert(0, grader_dir)
    from grader.config import load_standards
    from grader.extract import URL, api_keys, key_attempts, parse_response
    from grader.http import OVERLOADED, first_success, post_with_retry
    from grader.pipeline import load_env

    load_env()
    if not api_keys():
        raise RuntimeError('No Gemini key')
    standards, _, _ = load_standards(str(ROOT / 'softskills-grader' / 'config'))
    cfg = standards['apis']['gemini']
    request = {
        'system_instruction': SYSTEM,
        'input': (f'Topic question: {topic}\n'
                  f'Transcript (speech-to-text): <transcript>{transcript}</transcript>\n'
                  f'Items (one line of advice for each, same metric names):\n{json.dumps(items, indent=1)}'),
        'generation_config': {'thinking_level': cfg.get('thinking_level', 'low')},
        'response_format': {'type': 'text', 'mime_type': 'application/json', 'schema': SCHEMA},
        'store': False,
    }
    models = [cfg['model']] + [m for m in cfg.get('fallback_models') or [] if m != cfg['model']]
    attempts = key_attempts(models, lambda model, headers, over: post_with_retry(
        URL, deadline=20, retry_on=OVERLOADED, cancelled=over, headers=headers, json={**request, 'model': model}))
    response, _ = first_success(attempts, hedge_after=8)
    wanted = {item['metric'] for item in items}
    answer = parse_response(response.json())
    lines = {}
    for entry in answer.get('items') or []:
        line = clean_line(entry.get('advice'))
        if entry.get('metric') in wanted and line:
            lines[entry['metric']] = line
    if not lines:
        raise RuntimeError('Gemini returned no usable advice')
    return lines
