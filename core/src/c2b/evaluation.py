"""Repeatable, evidence-level regression checks for scanned code graphs.

This module deliberately evaluates deterministic graph facts only.  It does
not label an LLM business conclusion as "correct"; semantic quality requires
an independent human reviewer and is recorded separately in the quality
baseline documentation.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models


class EvaluationError(ValueError):
    """A supplied quality fixture is invalid or cannot target a scan."""


def load_manifest(path: Path) -> dict[str, Any]:
    """Load a versioned JSON manifest containing known source facts."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"无法读取质量基线清单：{exc}") from exc
    if manifest.get("schema_version") != 1:
        raise EvaluationError("质量基线清单必须使用 schema_version: 1")
    assertions = manifest.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        raise EvaluationError("质量基线清单必须至少包含一条 assertions")
    return manifest


def evaluate_scan(session: Session, project_id: int, manifest: dict[str, Any]) -> dict[str, Any]:
    """Evaluate exact API/table/SQL facts against the newest completed scan."""
    scan = session.execute(
        select(models.Scan)
        .where(models.Scan.project_id == project_id,
               models.Scan.status == models.ScanStatus.DONE)
        .order_by(models.Scan.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if scan is None:
        raise EvaluationError(f"项目 {project_id} 没有已完成扫描")

    supported = {"api", "table", "sql"}
    results: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    passed: Counter[str] = Counter()
    for assertion in manifest["assertions"]:
        if not isinstance(assertion, dict):
            raise EvaluationError("每条 assertion 必须是对象")
        kind = assertion.get("kind")
        assertion_id = assertion.get("id")
        if kind not in supported or not isinstance(assertion_id, str) or not assertion_id:
            raise EvaluationError("assertion 必须包含非空 id 和 api/table/sql kind")
        counts[kind] += 1
        matched = _matches(session, scan.id, assertion)
        if matched:
            passed[kind] += 1
        results.append({
            "id": assertion_id,
            "kind": kind,
            "passed": matched,
            "expected": {k: v for k, v in assertion.items() if k not in {"id", "kind"}},
        })

    total = sum(counts.values())
    success = sum(passed.values())
    by_kind = {
        kind: {
            "passed": passed[kind],
            "total": counts[kind],
            "rate": round(passed[kind] / counts[kind], 4) if counts[kind] else None,
        }
        for kind in sorted(counts)
    }
    return {
        "schema_version": 1,
        "sample": manifest.get("sample", {}),
        "project_id": project_id,
        "scan_id": scan.id,
        "git_commit": scan.git_commit,
        "scan_stats": scan.stats or {},
        "summary": {
            "passed": success,
            "total": total,
            "rate": round(success / total, 4) if total else None,
            "by_kind": by_kind,
        },
        "assertions": results,
        "missing": [item["id"] for item in results if not item["passed"]],
    }


def _matches(session: Session, scan_id: int, assertion: dict[str, Any]) -> bool:
    kind = assertion["kind"]
    if kind == "api":
        method = assertion.get("method")
        path = assertion.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            raise EvaluationError("api assertion 必须包含 method 和 path")
        return session.execute(
            select(models.ApiEndpoint.id).where(
                models.ApiEndpoint.scan_id == scan_id,
                models.ApiEndpoint.method == method.upper(),
                models.ApiEndpoint.path == path,
            ).limit(1)
        ).scalar_one_or_none() is not None
    if kind == "table":
        name = assertion.get("name")
        if not isinstance(name, str):
            raise EvaluationError("table assertion 必须包含 name")
        return session.execute(
            select(models.DataTable.id).where(
                models.DataTable.scan_id == scan_id,
                models.DataTable.name == name,
            ).limit(1)
        ).scalar_one_or_none() is not None
    table_name, op = assertion.get("table"), assertion.get("op")
    if not isinstance(table_name, str) or not isinstance(op, str):
        raise EvaluationError("sql assertion 必须包含 table 和 op")
    try:
        sql_op = models.SqlOp(op.lower())
    except ValueError as exc:
        raise EvaluationError(f"不支持的 SQL 操作类型：{op}") from exc
    return session.execute(
        select(models.SqlOperation.id).where(
            models.SqlOperation.scan_id == scan_id,
            models.SqlOperation.table_name == table_name,
            models.SqlOperation.op == sql_op,
        ).limit(1)
    ).scalar_one_or_none() is not None
