#!/usr/bin/env python3
"""Validate and summarize the committed independent semantic review sample."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


VERDICTS = {
    "correct",
    "partly_correct",
    "incorrect",
    "insufficient_evidence",
    "not_applicable",
}
RULE_TYPES = {
    "validation", "calculation", "status", "permission",
    "trigger", "exception", "other",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review", type=Path)
    parser.add_argument("--expected-count", type=int, default=50)
    args = parser.parse_args()

    with args.review.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    errors: list[str] = []
    if len(rows) != args.expected_count:
        errors.append(f"expected {args.expected_count} rows, found {len(rows)}")
    ids = [row.get("artifact_id", "").strip() for row in rows]
    if any(not item for item in ids):
        errors.append("every row must have artifact_id")
    if len(ids) != len(set(ids)):
        errors.append("artifact_id values must be unique")
    for index, row in enumerate(rows, start=2):
        verdict = row.get("verdict", "").strip()
        rule_type = row.get("rule_type", "").strip().lower()
        if verdict not in VERDICTS:
            errors.append(f"line {index}: invalid verdict {verdict!r}")
        if rule_type not in RULE_TYPES:
            errors.append(f"line {index}: invalid rule_type {rule_type!r}")
        if not row.get("reason", "").strip():
            errors.append(f"line {index}: reason is required")
        if verdict in {"partly_correct", "incorrect"} and not row.get(
            "corrected_summary", ""
        ).strip():
            errors.append(
                f"line {index}: {verdict} requires corrected_summary"
            )
    if errors:
        print("Semantic review validation failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1

    verdicts = Counter(row["verdict"].strip() for row in rows)
    types = Counter(row["rule_type"].strip().lower() for row in rows)
    denominator = len(rows) - verdicts["not_applicable"]
    summary = {
        "sample_size": len(rows),
        "verdicts": dict(sorted(verdicts.items())),
        "rule_types": dict(sorted(types.items())),
        "correct_rate": round(verdicts["correct"] / denominator, 4),
        "correct_or_partly_rate": round(
            (verdicts["correct"] + verdicts["partly_correct"]) / denominator,
            4,
        ),
        "rate_denominator": denominator,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
