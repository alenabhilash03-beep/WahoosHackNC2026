#!/usr/bin/env python3
"""Grade a recording from the command line.

    python grade.py speech.webm --topic "Should the frontier of AI be paced?" --side yes
    python grade.py --list-metrics
"""
import argparse
import json
import sys

from grader import grade
from grader.features import CATALOG


def print_report(report: dict) -> None:
    overall = report["overall"]
    print(f"\nOVERALL  {overall['score']:5.1f}  {overall['letter']}")
    if not report["gradable"]:
        print(f"Not gradable: {report['reason']}")
        return
    for name, factor in report["factors"].items():
        score = "  n/a" if factor["score"] is None else f"{factor['score']:5.1f}"
        print(f"\n{name.upper():<8} {score}   weight {factor['weight_pct']:g}%   "
              f"(base {factor['base']}, deducted {factor['deducted']:g})")
        for row in factor["metrics"]:
            if row["value"] is None:
                print(f"    {row['label']:<44} not measured")
            else:
                print(f"    {row['label']:<44} {row['value']:>7g} {row['unit']:<14} "
                      f"-> {row['score']:g}  [{row['bucket']}]")
        for row in factor["deductions"]:
            if row["points_lost"]:
                print(f"    - {row['points_lost']:g} pts: {row['label']} = {row['value']:g}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", nargs="?", help="recording (webm, mp3, wav, m4a ...)")
    parser.add_argument("--topic", default="Should the frontier of AI be paced?")
    parser.add_argument("--side", choices=["yes", "no"])
    parser.add_argument("--config", help="folder holding standards.yaml and lexicons.yaml")
    parser.add_argument("--cache", help="folder for cached transcripts and extractions")
    parser.add_argument("--out", help="write the full JSON report here")
    parser.add_argument("--list-metrics", action="store_true", help="show every metric you can use in standards.yaml")
    args = parser.parse_args()

    if args.list_metrics:
        for name, (label, unit, source) in CATALOG.items():
            print(f"{name:<32} {label}  [{unit}]  <- {source}")
        return 0
    if not args.audio or not args.side:
        parser.error("audio and --side are required")

    report = grade(args.audio, args.topic, args.side, config_dir=args.config, cache_dir=args.cache)
    print_report(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
        print(f"\nFull report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
