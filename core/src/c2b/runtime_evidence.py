"""Runtime-evidence helpers shared by APIs, exports, and semantic refreshes.

Static evidence (V), runtime verification (R), and human confirmation are
separate axes.  This module only handles the R axis.
"""
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

RUNTIME_LEVEL_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}


def runtime_level(items: Iterable[models.RuntimeEvidence]) -> str:
    """Return the strongest recorded runtime level, or derived R0."""
    return max(
        (item.level for item in items),
        key=lambda value: RUNTIME_LEVEL_ORDER.get(value, -1),
        default="R0",
    )


def evidence_by_target(
    s: Session,
    scan_id: int,
    target_type: str,
    target_ids: Iterable[int],
) -> dict[int, list[models.RuntimeEvidence]]:
    ids = list(dict.fromkeys(target_ids))
    if not ids:
        return {}
    rows = s.execute(
        select(models.RuntimeEvidence).where(
            models.RuntimeEvidence.scan_id == scan_id,
            models.RuntimeEvidence.target_type == target_type,
            models.RuntimeEvidence.target_id.in_(ids),
        ).order_by(models.RuntimeEvidence.id.desc())
    ).scalars().all()
    grouped: dict[int, list[models.RuntimeEvidence]] = {}
    for row in rows:
        grouped.setdefault(row.target_id, []).append(row)
    return grouped


def serialize_evidence(item: models.RuntimeEvidence) -> dict:
    return {
        "id": item.id,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "level": item.level,
        "evidence_type": item.evidence_type,
        "environment": item.environment,
        "summary": item.summary,
        "reference": item.reference,
        "observed_at": str(item.observed_at) if item.observed_at else None,
        "created_by": item.created_by,
        "created_at": str(item.created_at),
    }


def delete_for_targets(
    s: Session,
    scan_id: int,
    target_type: str,
    target_ids: Iterable[int],
) -> int:
    """Delete evidence before generated semantic targets are replaced."""
    ids = list(dict.fromkeys(target_ids))
    if not ids:
        return 0
    return s.query(models.RuntimeEvidence).filter(
        models.RuntimeEvidence.scan_id == scan_id,
        models.RuntimeEvidence.target_type == target_type,
        models.RuntimeEvidence.target_id.in_(ids),
    ).delete(synchronize_session=False)
