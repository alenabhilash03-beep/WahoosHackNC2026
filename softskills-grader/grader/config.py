"""Load and check the standards. A typo in the rubric fails loudly here, not mid-grade."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import yaml

from .features import CATALOG

LEXICONS = ("fillers", "hedges", "connectives", "audience_words", "emotion_words")


class StandardsError(ValueError):
    pass


def _number(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StandardsError(f"{where}: expected a number, got {value!r}")
    return float(value)


def _check_buckets(rule: dict, where: str) -> None:
    buckets = rule.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        raise StandardsError(f"{where}: needs a non-empty 'buckets' list")
    spans = []
    for i, bucket in enumerate(buckets):
        spot = f"{where} bucket {i + 1}"
        extra = set(bucket) - {"from", "to", "score"}
        if extra:
            raise StandardsError(f"{spot}: unknown keys {sorted(extra)} (use from / to / score)")
        low = _number(bucket["from"], spot) if "from" in bucket else -math.inf
        high = _number(bucket["to"], spot) if "to" in bucket else math.inf
        score = _number(bucket.get("score"), spot)
        if not low < high:
            raise StandardsError(f"{spot}: 'from' must be below 'to'")
        if not 0 <= score <= 100:
            raise StandardsError(f"{spot}: score must be between 0 and 100")
        spans.append((low, high, i + 1))
    spans.sort()
    for (_, high, a), (low, _, b) in zip(spans, spans[1:]):
        if low < high:
            raise StandardsError(f"{where}: buckets {a} and {b} overlap")
    if "else" in rule and not 0 <= _number(rule["else"], where) <= 100:
        raise StandardsError(f"{where}: 'else' must be between 0 and 100")


def _check_critical(rule: dict, where: str) -> None:
    tests = [key for key in ("score_below", "below", "at_least") if key in rule]
    if len(tests) != 1:
        raise StandardsError(f"{where}: needs exactly one of score_below, below or at_least")
    extra = set(rule) - {"metric", "score_below", "below", "at_least", "cap", "reason"}
    if extra:
        raise StandardsError(f"{where}: unknown keys {sorted(extra)}")
    if tests[0] == "score_below":
        if "metric" in rule:
            raise StandardsError(f"{where}: score_below applies to the whole factor; remove 'metric'")
    elif rule.get("metric") not in CATALOG:
        raise StandardsError(f"{where}: unknown metric {rule.get('metric')!r}")
    _number(rule[tests[0]], where)
    if not 0 <= _number(rule.get("cap"), where + " cap") <= 100:
        raise StandardsError(f"{where}: cap must be between 0 and 100")
    if not isinstance(rule.get("reason"), str) or not rule["reason"].strip():
        raise StandardsError(f"{where}: needs a 'reason' to show the player")


def validate(standards: dict, lexicons: dict) -> None:
    for section in ("session", "analysis", "apis", "grades", "factors"):
        if section not in standards:
            raise StandardsError(f"standards.yaml is missing the '{section}' section")
    checker = standards["apis"].get("stance_checker", {})
    fields = {"model", "thinking_level", "confirmation_model", "confirmation_thinking_level"}
    if not isinstance(checker, dict) or set(checker) - fields:
        raise StandardsError("apis.stance_checker: expected model/thinking_level and confirmation_model/confirmation_thinking_level")
    for key, value in checker.items():
        if not isinstance(value, str) or not value.strip():
            raise StandardsError(f"apis.stance_checker.{key}: expected a non-empty string")
        if key.endswith("thinking_level") and value not in ("minimal", "low", "medium", "high"):
            raise StandardsError(f"apis.stance_checker.{key}: expected minimal, low, medium or high")
    factors = standards["factors"]
    if not isinstance(factors, dict) or not factors:
        raise StandardsError("standards.yaml: 'factors' is empty")
    for name, factor in factors.items():
        where = f"factor '{name}'"
        if _number(factor.get("weight"), where + " weight") <= 0:
            raise StandardsError(f"{where}: weight must be above 0")
        metrics = factor.get("metrics") or {}
        if not metrics:
            raise StandardsError(f"{where}: needs at least one metric")
        for metric, rule in metrics.items():
            spot = f"{where} metric '{metric}'"
            if metric not in CATALOG:
                raise StandardsError(f"{spot}: unknown metric. Known: {', '.join(sorted(CATALOG))}")
            if _number(rule.get("weight"), spot + " weight") <= 0:
                raise StandardsError(f"{spot}: weight must be above 0")
            _check_buckets(rule, spot)
        for i, rule in enumerate(factor.get("deductions") or []):
            spot = f"{where} deduction {i + 1}"
            if rule.get("metric") not in CATALOG:
                raise StandardsError(f"{spot}: unknown metric {rule.get('metric')!r}")
            if _number(rule.get("points_each"), spot) <= 0:
                raise StandardsError(f"{spot}: points_each must be above 0")
            if _number(rule.get("max_points"), spot) < rule["points_each"]:
                raise StandardsError(f"{spot}: max_points must be at least points_each")
            if _number(rule.get("free", 0), spot) < 0:
                raise StandardsError(f"{spot}: free cannot be negative")
        for i, rule in enumerate(factor.get("critical") or []):
            _check_critical(rule, f"{where} critical rule {i + 1}")
    for name in LEXICONS:
        if not isinstance(lexicons.get(name), list):
            raise StandardsError(f"lexicons.yaml is missing the '{name}' list")


def load_standards(config_dir: str) -> tuple[dict, dict, str]:
    folder = Path(config_dir)
    standards = yaml.safe_load((folder / "standards.yaml").read_text(encoding="utf-8"))
    lexicons = yaml.safe_load((folder / "lexicons.yaml").read_text(encoding="utf-8"))
    validate(standards, lexicons)
    blob = json.dumps({"standards": standards, "lexicons": lexicons}, sort_keys=True, separators=(",", ":"))
    return standards, lexicons, hashlib.sha256(blob.encode()).hexdigest()[:16]
