"""The grading engine: measured numbers + standards -> sub-grades and a main grade.

A pure function. No randomness, no network, no model. Same inputs, same grade.
"""
from __future__ import annotations

import math

from .features import CATALOG


def _fmt(number: float) -> str:
    return f"{number:g}"


def bucket_score(value: float, rule: dict) -> tuple[float, str]:
    for bucket in rule["buckets"]:
        low, high = bucket.get("from", -math.inf), bucket.get("to", math.inf)
        if low <= value < high:
            if low == -math.inf:
                label = f"under {_fmt(high)}"
            elif high == math.inf:
                label = f"{_fmt(low)} or more"
            else:
                label = f"{_fmt(low)} to under {_fmt(high)}"
            return float(bucket["score"]), label
    return float(rule.get("else", 0)), "outside every bucket"


def score_factor(factor: dict, features: dict) -> dict:
    rows, total_weight, weighted = [], 0.0, 0.0
    for name, rule in factor["metrics"].items():
        label, unit, source = CATALOG[name]
        value = features.get(name)
        row = {"metric": name, "label": label, "unit": unit, "source": source, "value": value,
               "weight": rule["weight"], "bucket": None, "score": None}
        if value is not None:
            row["score"], row["bucket"] = bucket_score(value, rule)
            total_weight += rule["weight"]
            weighted += rule["weight"] * row["score"]
        rows.append(row)

    if total_weight == 0:
        return {"score": None, "base": None, "deducted": 0.0, "metrics": rows, "deductions": []}
    base = weighted / total_weight

    deductions, lost = [], 0.0
    for rule in factor.get("deductions") or []:
        value = features.get(rule["metric"])
        if value is None:
            continue
        over = max(0.0, value - rule.get("free", 0))
        points = min(float(rule["max_points"]), over * rule["points_each"])
        deductions.append({"metric": rule["metric"], "label": CATALOG[rule["metric"]][0], "value": value,
                           "free": rule.get("free", 0), "points_each": rule["points_each"],
                           "max_points": rule["max_points"], "points_lost": round(points, 2)})
        lost += points

    return {"score": round(min(100.0, max(0.0, base - lost)), 1), "base": round(base, 1),
            "deducted": round(lost, 2), "metrics": rows, "deductions": deductions}


def letter_for(score: float, grades: list[dict]) -> str:
    for grade in sorted(grades, key=lambda g: -g["from"]):
        if score >= grade["from"]:
            return grade["letter"]
    return grades[-1]["letter"]


def check_critical(name: str, factor: dict, result: dict, features: dict) -> list[dict]:
    """Evaluate a factor's critical rules. Each one that triggers caps the overall grade."""
    rows = []
    for rule in factor.get("critical") or []:
        if "score_below" in rule:
            metric, label = f"{name}_score", f"{name.capitalize()} score"
            value, limit = result["score"], rule["score_below"]
            triggered = value is not None and value < limit
            condition = f"below {_fmt(limit)}"
        else:
            metric, label = rule["metric"], CATALOG[rule["metric"]][0]
            value = features.get(metric)
            if "below" in rule:
                limit, condition = rule["below"], f"below {_fmt(rule['below'])}"
                triggered = value is not None and value < limit
            else:
                limit, condition = rule["at_least"], f"{_fmt(rule['at_least'])} or more"
                triggered = value is not None and value >= limit
        rows.append({"metric": metric, "label": label, "value": value, "condition": condition,
                     "cap": float(rule["cap"]), "reason": rule["reason"], "triggered": triggered})
    return rows


def score_all(features: dict, standards: dict) -> dict:
    factors, total_weight, weighted = {}, 0.0, 0.0
    for name, factor in standards["factors"].items():
        result = score_factor(factor, features)
        result["weight"] = factor["weight"]
        result["critical"] = check_critical(name, factor, result, features)
        factors[name] = result
        if result["score"] is not None:
            total_weight += factor["weight"]
            weighted += factor["weight"] * result["score"]
    uncapped = round(weighted / total_weight, 1) if total_weight else 0.0
    for result in factors.values():
        counted = result["score"] is not None and total_weight
        result["weight_pct"] = round(100.0 * result["weight"] / total_weight, 1) if counted else 0.0

    # A critical failure caps the overall grade no matter how good the other parts are.
    # Every triggered rule whose cap is below the average is listed, lowest cap first.
    capped_by = sorted(({**row, "factor": name} for name, result in factors.items()
                        for row in result["critical"] if row["triggered"] and row["cap"] < uncapped),
                       key=lambda row: row["cap"])
    overall = min([uncapped] + [row["cap"] for row in capped_by])
    return {"overall": {"score": overall, "letter": letter_for(overall, standards["grades"]),
                        "uncapped": uncapped, "capped_by": capped_by},
            "factors": factors}
