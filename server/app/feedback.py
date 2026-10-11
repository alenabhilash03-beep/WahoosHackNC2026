"""Pure solo card calculations. Losses are rubric attributions, before factor clipping."""
from datetime import datetime
import math
from .tips import tip_for

FACTOR_ORDER = ('logic', 'emotion', 'tone', 'diction', 'pace')


def measured(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def delta(current, before):
    return round(current - before, 1) if measured(current) and measured(before) else None


def graded(row):
    report = row.get('report') or {}
    return (row.get('status') in ('done', 'graded') and report.get('gradable') is True
            and measured((report.get('overall') or {}).get('score')))


def factor_scores(report):
    factors = report.get('factors') or {}
    return {key: factors[key]['score'] for key in FACTOR_ORDER
            if measured((factors.get(key) or {}).get('score'))}


def losses(report):
    if not report.get('gradable'):
        return []
    items = []
    for name in FACTOR_ORDER:
        factor = (report.get('factors') or {}).get(name)
        if not factor or not measured(factor.get('score')) or not measured(factor.get('weight_pct')) or factor['weight_pct'] <= 0:
            continue
        weight = factor['weight_pct'] / 100
        metrics = [metric for metric in factor.get('metrics', [])
                   if measured(metric.get('score')) and measured(metric.get('weight')) and metric['weight'] > 0]
        denominator = sum(metric['weight'] for metric in metrics)
        for kind, entries in (('metric', metrics), ('deduction', factor.get('deductions', []))):
            for metric in entries:
                if kind == 'deduction' and not measured(metric.get('points_lost')):
                    continue
                loss = weight * (metric['weight'] / denominator) * (100 - metric['score']) if kind == 'metric' and denominator else weight * metric.get('points_lost', 0)
                if loss > 0:
                    items.append(dict(factor=name, metric=metric['metric'], label=metric['label'], kind=kind,
                                      lost_points=round(loss, 8), tip=tip_for(metric)))
    # A critical failure caps the overall below the average of the parts. The binding
    # (lowest) cap owns the gap, so the losses still add up to 100 minus the overall.
    overall = report.get('overall') or {}
    capped_by = overall.get('capped_by') or []
    if capped_by and measured(overall.get('uncapped')) and measured(overall.get('score')):
        gap = overall['uncapped'] - overall['score']
        rule = capped_by[0]
        if gap > 0:
            items.append(dict(factor=rule['factor'], metric=rule['metric'], label=rule['reason'], kind='critical',
                              lost_points=round(gap, 8), tip=tip_for(rule)))
    return items


def top_fixes(report):
    return sorted(losses(report), key=lambda item: -item['lost_points'])[:3]


def recording_time(row):
    return datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))


def previous_best(current, rows):
    candidates = [row for row in rows if graded(row) and row['motion_id'] == current['motion_id']
                  and row['side'] == current['side'] and recording_time(row) < recording_time(current)]
    return min(candidates, key=lambda row: (-row['report']['overall']['score'], recording_time(row), row['id'])) if candidates else None


def section_scores(report):
    factors = report.get('factors') or {}
    def single(name):
        value = (factors.get(name) or {}).get('score')
        return value if measured(value) else None
    delivery = [factors[key] for key in ('pace', 'diction', 'tone')
                if measured((factors.get(key) or {}).get('score'))
                and measured(factors[key].get('weight_pct')) and factors[key]['weight_pct'] > 0]
    denom = sum(f['weight_pct'] for f in delivery)
    return dict(logos=single('logic'), pathos=single('emotion'),
                delivery=round(sum(f['score'] * f['weight_pct'] for f in delivery) / denom, 1) if denom else None)


def speech_card(row, history, prompt, audio_url):
    report = row.get('report')
    prior = previous_best(row, history) if graded(row) else None
    scores = section_scores(report or {}) if graded(row) else dict(logos=None, pathos=None, delivery=None)
    factors = (report or {}).get('factors') or {}
    evidence = (report or {}).get('evidence') or {}
    sections = {
        'logos': dict(score=scores['logos'], factors={'logic': factors['logic']} if 'logic' in factors else {}, evidence=evidence.get('argument', {})),
        'pathos': dict(score=scores['pathos'], factors={'emotion': factors['emotion']} if 'emotion' in factors else {}, evidence={'emotional_appeals': evidence.get('argument', {}).get('emotional_appeals', [])}),
        'delivery': dict(score=scores['delivery'], factors={key: factors[key] for key in ('pace', 'diction', 'tone') if key in factors}, evidence={key: evidence.get(key, {}) for key in ('pace', 'diction', 'tone')}),
    }
    deltas = None
    previous = None
    if prior:
        before = prior['report']
        before_sections = section_scores(before)
        current_factors, prior_factors = factor_scores(report), factor_scores(before)
        deltas = {'overall': delta(report['overall']['score'], before['overall']['score']),
                  **{key: delta(scores[key], before_sections[key]) for key in scores},
                  'factors': {key: delta(current_factors.get(key), prior_factors.get(key))
                              for key in FACTOR_ORDER if key in current_factors or key in prior_factors}}
        previous = dict(id=prior['id'], overall=before['overall'], factor_scores=factor_scores(before))
    return dict(id=row['id'], status=row['status'], motion_id=row['motion_id'], side=row['side'], prompt=prompt,
                report=report, audio_url=audio_url, fixes=top_fixes(report or {}), sections=sections,
                deltas=deltas, previous_best=previous, error=row.get('error'))


def me_card(profile, rows, prompts):
    ordered = sorted(rows, key=lambda row: (recording_time(row), row['id']))
    valid = [row for row in ordered if graded(row)]
    bests = {}
    for row in valid:
        key = row['motion_id']
        if key not in bests or row['report']['overall']['score'] > bests[key]['report']['overall']['score']:
            bests[key] = row
    return dict(id=profile['id'], display_name=profile['display_name'], prompts=prompts,
                speeches=[dict(id=row['id'], motion_id=row['motion_id'], side=row['side'], created_at=row['created_at'], status=row['status'],
                               overall=row['report']['overall'] if graded(row) else None,
                               factor_scores=factor_scores(row['report']) if graded(row) else None) for row in ordered],
                bests=[dict(motion_id=key, speech_id=row['id'], overall=row['report']['overall'], factor_scores=factor_scores(row['report'])) for key, row in bests.items()],
                recent_overalls=[dict(speech_id=row['id'], score=row['report']['overall']['score'], created_at=row['created_at']) for row in valid[-10:]],
                season_tier=None, judge_xp=0)
