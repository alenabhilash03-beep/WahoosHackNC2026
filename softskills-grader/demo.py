#!/usr/bin/env python3
"""See the grader work with no API keys.

Builds a synthetic one-minute recording plus canned ElevenLabs and Gemini
responses (tests/fixtures.py), grades it, and writes sample_report.json.
The "voice" is a synthetic buzz, not real speech: this shows the mechanics.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))

import fixtures  # noqa: E402
from grade import print_report  # noqa: E402
from grader import grade  # noqa: E402

with tempfile.TemporaryDirectory() as folder:
    fx = fixtures.build(folder)
    report = grade(fx["audio"], fx["topic"], "yes", cache_dir=folder,
                   transcript=fx["scribe"], extraction=fx["annotations"], stance_checks=fx["stance_checks"])
print_report(report)
(ROOT / "sample_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
print("\nFull report written to sample_report.json")
