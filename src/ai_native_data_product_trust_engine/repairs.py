"""Repair candidate generation and controlled repair execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ai_native_data_product_trust_engine.models import RepairMode, TestStatus, ValidationRun


@dataclass(frozen=True)
class RepairCandidate:
    issue_code: str
    summary: str
    mode: RepairMode
    sql: str | None = None
    requires_approval: bool = True
    candidate_id: str = ""
    test_id: str = ""
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RepairApplicationResult:
    candidate: RepairCandidate
    applied: bool
    error_message: str | None = None


def classify_stale_relationship_path_name(object_name: str) -> RepairCandidate | None:
    if object_name.lower() not in {"v_relationship_paths", "v_relationship_patsh"}:
        return None

    return RepairCandidate(
        candidate_id="STALE-RELATIONSHIP-PATH-NAME",
        issue_code="STALE_OBJECT_NAME",
        summary="Replace stale v_relationship_paths reference with relationship_paths.",
        mode=RepairMode.SAFE_AUTO,
        requires_approval=False,
    )


def generate_repair_candidates(run: ValidationRun) -> list[RepairCandidate]:
    candidates: list[RepairCandidate] = []
    for result in run.results:
        if result.status == TestStatus.PASSED:
            continue
        for sample_row in result.sample_rows or [{}]:
            candidate = _candidate_from_sample(result.test_case.test_id, sample_row)
            if candidate:
                candidates.append(candidate)
    return candidates


def apply_safe_repairs(adapter, candidates: list[RepairCandidate]) -> list[RepairApplicationResult]:
    results: list[RepairApplicationResult] = []
    for candidate in candidates:
        if candidate.requires_approval or candidate.mode != RepairMode.SAFE_AUTO or not candidate.sql:
            continue
        try:
            adapter.execute(candidate.sql)
        except Exception as exc:  # noqa: BLE001 - repair failures are evidence, not tool crashes.
            results.append(
                RepairApplicationResult(
                    candidate=candidate,
                    applied=False,
                    error_message=str(exc),
                )
            )
            continue
        results.append(RepairApplicationResult(candidate=candidate, applied=True))
    return results


def write_repair_reports(candidates: list[RepairCandidate], output_path: Path) -> tuple[Path, Path]:
    markdown_path = output_path.with_suffix(".repairs.md")
    sql_path = output_path.with_suffix(".repairs.sql")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_repair_markdown(candidates), encoding="utf-8")
    sql_path.write_text(_repair_sql(candidates), encoding="utf-8")
    return markdown_path, sql_path


def _candidate_from_sample(test_id: str, sample_row: dict[str, object]) -> RepairCandidate | None:
    if _is_safe_text_alias(sample_row):
        return _safe_text_alias_candidate(test_id, sample_row)

    issue_code = str(sample_row.get("issue_code") or test_id)
    repair_hint = str(sample_row.get("repair_hint") or "Review the metadata contract and decide the repair.")
    summary = f"{issue_code}: {repair_hint}"
    return RepairCandidate(
        candidate_id=_candidate_id(test_id, issue_code, sample_row),
        issue_code=issue_code,
        summary=summary,
        mode=RepairMode.PROPOSAL,
        requires_approval=True,
        test_id=test_id,
        evidence=sample_row,
    )


def _is_safe_text_alias(sample_row: dict[str, object]) -> bool:
    return bool(
        sample_row.get("safe_auto_apply")
        and sample_row.get("database_name")
        and sample_row.get("table_name")
        and sample_row.get("column_name")
        and sample_row.get("token")
        and sample_row.get("replacement")
        and sample_row.get("key_values")
    )


def _safe_text_alias_candidate(test_id: str, sample_row: dict[str, object]) -> RepairCandidate:
    token = str(sample_row["token"])
    replacement = str(sample_row["replacement"])
    sql = _safe_text_update_sql(sample_row)
    return RepairCandidate(
        candidate_id=_candidate_id(test_id, "STALE_OBJECT_NAME", sample_row),
        issue_code="STALE_OBJECT_NAME",
        summary=f"Replace {token} with {replacement} in {sample_row['database_name']}.{sample_row['table_name']}.{sample_row['column_name']}.",
        mode=RepairMode.SAFE_AUTO,
        sql=sql,
        requires_approval=False,
        test_id=test_id,
        evidence=sample_row,
    )


def _safe_text_update_sql(sample_row: dict[str, object]) -> str:
    database_name = _identifier(str(sample_row["database_name"]))
    table_name = _identifier(str(sample_row["table_name"]))
    column_name = _identifier(str(sample_row["column_name"]))
    token = _sql_string(str(sample_row["token"]))
    replacement = _sql_string(str(sample_row["replacement"]))
    key_values = sample_row["key_values"]
    if not isinstance(key_values, dict):
        msg = "[ADPTrust.InvalidRepairEvidence] key_values must be present for safe text repair."
        raise ValueError(msg)
    where_clause = " AND ".join(
        f"{_identifier(str(column_name))} = {_sql_literal(value)}"
        for column_name, value in key_values.items()
    )
    return (
        f"UPDATE {database_name}.{table_name}\n"
        f"SET {column_name} = CAST(\n"
        f"    OREPLACE(CAST({column_name} AS VARCHAR(32000)), {token}, {replacement})\n"
        f"    AS CLOB(32000)\n"
        f")\n"
        f"WHERE {where_clause};"
    )


def _repair_markdown(candidates: list[RepairCandidate]) -> str:
    lines = ["# Repair Candidates", ""]
    if not candidates:
        lines.append("No repair candidates generated.")
        return "\n".join(lines) + "\n"
    for candidate in candidates:
        approval = "requires approval" if candidate.requires_approval else "safe-auto"
        lines.extend(
            [
                f"## {candidate.candidate_id}",
                "",
                f"- Issue: `{candidate.issue_code}`",
                f"- Mode: `{candidate.mode.value}`",
                f"- Approval: {approval}",
                f"- Source test: `{candidate.test_id}`",
                f"- Summary: {candidate.summary}",
            ]
        )
        if candidate.sql:
            lines.extend(["", "```sql", candidate.sql, "```"])
        lines.append("")
    return "\n".join(lines)


def _repair_sql(candidates: list[RepairCandidate]) -> str:
    sql_blocks = [
        candidate.sql
        for candidate in candidates
        if candidate.sql and not candidate.requires_approval and candidate.mode == RepairMode.SAFE_AUTO
    ]
    if not sql_blocks:
        return "-- No safe-auto SQL repair candidates generated.\n"
    return "\n\n".join(sql_blocks) + "\n"


def _candidate_id(test_id: str, issue_code: str, sample_row: dict[str, object]) -> str:
    row_id = (
        sample_row.get("recipe_id")
        or sample_row.get("relationship_name")
        or sample_row.get("row_key")
        or sample_row.get("missing_object")
        or sample_row.get("missing_column")
        or "metadata"
    )
    raw = f"{test_id}-{issue_code}-{row_id}"
    return "".join(char if char.isalnum() else "-" for char in str(raw)).strip("-").upper()


def _identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        msg = f"[ADPTrust.InvalidIdentifier] Unsafe SQL identifier {value}."
        raise ValueError(msg)
    return value


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_literal(value: object) -> str:
    if isinstance(value, int | float):
        return str(value)
    return _sql_string(str(value))
