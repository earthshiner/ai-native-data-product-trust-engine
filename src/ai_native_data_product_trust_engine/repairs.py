"""Repair classification primitives."""

from __future__ import annotations

from dataclasses import dataclass

from ai_native_data_product_trust_engine.models import RepairMode


@dataclass(frozen=True)
class RepairCandidate:
    issue_code: str
    summary: str
    mode: RepairMode
    sql: str | None = None
    requires_approval: bool = True


def classify_stale_relationship_path_name(object_name: str) -> RepairCandidate | None:
    if object_name.lower() not in {"v_relationship_paths", "v_relationship_patsh"}:
        return None

    return RepairCandidate(
        issue_code="STALE_OBJECT_NAME",
        summary="Replace stale v_relationship_paths reference with relationship_paths.",
        mode=RepairMode.SAFE_AUTO,
        requires_approval=False,
    )
