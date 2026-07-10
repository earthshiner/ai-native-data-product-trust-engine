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

    if _is_entity_view_name_repair(sample_row):
        return _entity_view_name_candidate(test_id, sample_row)

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
    sql = _safe_text_repair_sql(sample_row)
    return RepairCandidate(
        candidate_id=_candidate_id(test_id, "STALE_OBJECT_NAME", sample_row),
        issue_code="STALE_OBJECT_NAME",
        summary=f"Replace {token} with {replacement} in {sample_row['database_name']}.{sample_row['table_name']}.{sample_row['column_name']} using a temporal successor row.",
        mode=RepairMode.SAFE_AUTO,
        sql=sql,
        requires_approval=False,
        test_id=test_id,
        evidence=sample_row,
    )


_ENTITY_VIEW_NAME_ISSUES = frozenset(
    {"ENTITY_VIEW_NAME_MISSING", "ENTITY_VIEW_NAME_NOT_DEPLOYED"}
)


def _is_entity_view_name_repair(sample_row: dict[str, object]) -> bool:
    """True when SEM-008 evidence carries the enrichment the generator needs."""
    return bool(
        str(sample_row.get("issue_code") or "") in _ENTITY_VIEW_NAME_ISSUES
        and sample_row.get("metadata_database_name")
        and sample_row.get("metadata_table_name")
        and sample_row.get("entity_metadata_id") is not None
    )


def _entity_view_name_candidate(test_id: str, sample_row: dict[str, object]) -> RepairCandidate:
    """Repair a SEM-008 entity whose ``view_name`` is missing or points nowhere.

    Only a *missing* ``view_name`` whose conventional BUS_V view is already
    deployed is safe to auto-populate — the target is then unambiguous. Every
    other shape (an irregularly-named approved view, a view that must be created,
    or an already-populated name whose view is not deployed) is emitted as a
    review proposal rather than an auto-applied statement.
    """
    issue_code = str(sample_row["issue_code"])
    entity_name = str(sample_row.get("entity_name") or "entity")
    metadata_database = _identifier(str(sample_row["metadata_database_name"]))
    metadata_table = _identifier(str(sample_row["metadata_table_name"]))
    entity_key = _sql_literal(sample_row["entity_metadata_id"])
    derived_view = str(sample_row.get("derived_view_name") or "").strip()
    derived_view_deployed = _as_int(sample_row.get("derived_view_deployed")) == 1

    if issue_code == "ENTITY_VIEW_NAME_MISSING" and derived_view_deployed and derived_view:
        sql = (
            f"UPDATE {metadata_database}.{metadata_table}\n"
            f"SET view_name = {_sql_string(derived_view)}\n"
            f"WHERE entity_metadata_id = {entity_key}\n"
            f"  AND view_name IS NULL;"
        )
        return RepairCandidate(
            candidate_id=_candidate_id(test_id, issue_code, sample_row),
            issue_code=issue_code,
            summary=(
                f"Populate {entity_name} entity_metadata.view_name = "
                f"{derived_view} (deployed BUS_V view)."
            ),
            mode=RepairMode.SAFE_AUTO,
            sql=sql,
            requires_approval=False,
            test_id=test_id,
            evidence=sample_row,
        )

    return RepairCandidate(
        candidate_id=_candidate_id(test_id, issue_code, sample_row),
        issue_code=issue_code,
        summary=_entity_view_name_proposal_summary(issue_code, entity_name, sample_row, derived_view),
        mode=RepairMode.PROPOSAL,
        sql=_entity_view_name_proposal_sql(
            issue_code,
            f"{metadata_database}.{metadata_table}",
            entity_key,
            entity_name,
            sample_row,
            derived_view,
        ),
        requires_approval=True,
        test_id=test_id,
        evidence=sample_row,
    )


def _entity_view_name_proposal_summary(
    issue_code: str, entity_name: str, sample_row: dict[str, object], derived_view: str
) -> str:
    if issue_code == "ENTITY_VIEW_NAME_NOT_DEPLOYED":
        view_name = str(sample_row.get("view_name") or "").strip()
        return (
            f"{entity_name}: entity_metadata.view_name '{view_name}' is not a deployed "
            "BUS_V view — deploy the referenced view, then re-run validation."
        )
    return (
        f"{entity_name}: entity_metadata.view_name is missing and the conventional BUS_V "
        f"view {derived_view or '(derived from table_name)'} is not deployed — confirm the "
        "approved view name (it may be non-conventional) or deploy it, then populate view_name."
    )


def _entity_view_name_proposal_sql(
    issue_code: str,
    metadata_target: str,
    entity_key: str,
    entity_name: str,
    sample_row: dict[str, object],
    derived_view: str,
) -> str:
    if issue_code == "ENTITY_VIEW_NAME_NOT_DEPLOYED":
        view_name = str(sample_row.get("view_name") or "").strip()
        return (
            f"-- PROPOSAL ({entity_name}): view_name '{view_name}' references a BUS_V view "
            "that is not deployed.\n"
            "-- Deploy the referenced view in its BUS_V database (e.g. via SHIPS), then "
            "re-run validation.\n"
            "-- No metadata change is required once the view is deployed."
        )
    return (
        f"-- PROPOSAL ({entity_name}): view_name is missing; the conventional view "
        f"{derived_view or '(derived from table_name)'} is not deployed.\n"
        "-- 1) Confirm the approved BUS_V access view for this entity (it may be "
        "non-conventional), or create it in the BUS_V database (e.g. via SHIPS).\n"
        "-- 2) Then populate the metadata:\n"
        f"-- UPDATE {metadata_target} SET view_name = '<approved_BUS_V_view>' "
        f"WHERE entity_metadata_id = {entity_key} AND view_name IS NULL;"
    )


def _safe_text_repair_sql(sample_row: dict[str, object]) -> str:
    table_name = _identifier(str(sample_row["table_name"]))
    if table_name == "Query_Cookbook":
        return _query_cookbook_temporal_repair_sql(sample_row)

    return _safe_text_update_sql(sample_row)


def _query_cookbook_temporal_repair_sql(sample_row: dict[str, object]) -> str:
    database_name = _repair_database_name(str(sample_row["database_name"]))
    table_name = _identifier(str(sample_row["table_name"]))
    column_name = _identifier(str(sample_row["column_name"]))
    token = _sql_string(str(sample_row["token"]))
    replacement = _sql_string(str(sample_row["replacement"]))
    key_values = sample_row["key_values"]
    if not isinstance(key_values, dict):
        msg = "[ADPTrust.InvalidRepairEvidence] key_values must be present for safe text repair."
        raise ValueError(msg)
    where_clause = _where_clause(key_values)
    repaired_expression = _clob_safe_replace_expression(column_name, token, replacement)
    return f"""
UPDATE {database_name}.{table_name}
SET is_active = 0
   ,valid_to = CURRENT_DATE - 1
   ,updated_timestamp = CURRENT_TIMESTAMP
WHERE {where_clause}
  AND is_active = 1;

INSERT INTO {database_name}.{table_name}
(
    recipe_key
   ,recipe_id
   ,recipe_title
   ,recipe_description
   ,use_case
   ,target_module
   ,sql_template
   ,parameter_descriptions
   ,performance_notes
   ,complexity
   ,source_module
   ,is_batch
   ,module_version
   ,is_active
   ,valid_from
   ,valid_to
   ,created_timestamp
   ,updated_timestamp
)
SELECT
    (SELECT COALESCE(MAX(recipe_key), 0) + 1 FROM {database_name}.{table_name}) AS recipe_key
   ,recipe_id
   ,recipe_title
   ,{repaired_expression if column_name == "recipe_description" else "recipe_description"} AS recipe_description
   ,use_case
   ,target_module
   ,{repaired_expression if column_name == "sql_template" else "sql_template"} AS sql_template
   ,parameter_descriptions
   ,{repaired_expression if column_name == "performance_notes" else "performance_notes"} AS performance_notes
   ,complexity
   ,source_module
   ,is_batch
   ,module_version
   ,1 AS is_active
   ,CURRENT_DATE AS valid_from
   ,CAST(NULL AS DATE) AS valid_to
   ,CURRENT_TIMESTAMP AS created_timestamp
   ,CURRENT_TIMESTAMP AS updated_timestamp
FROM {database_name}.{table_name}
WHERE {where_clause}
  AND valid_to = CURRENT_DATE - 1;
""".strip()


def _safe_text_update_sql(sample_row: dict[str, object]) -> str:
    database_name = _repair_database_name(str(sample_row["database_name"]))
    table_name = _identifier(str(sample_row["table_name"]))
    column_name = _identifier(str(sample_row["column_name"]))
    token = _sql_string(str(sample_row["token"]))
    replacement = _sql_string(str(sample_row["replacement"]))
    key_values = sample_row["key_values"]
    if not isinstance(key_values, dict):
        msg = "[ADPTrust.InvalidRepairEvidence] key_values must be present for safe text repair."
        raise ValueError(msg)
    where_clause = _where_clause(key_values)
    return (
        f"UPDATE {database_name}.{table_name}\n"
        f"SET {column_name} = {_clob_safe_replace_expression(column_name, token, replacement)}\n"
        f"WHERE {where_clause};"
    )


def _where_clause(key_values: dict[str, object]) -> str:
    return " AND ".join(
        f"{_identifier(str(column_name))} = {_sql_literal(value)}"
        for column_name, value in key_values.items()
    )


def _clob_safe_replace_expression(column_name: str, token: str, replacement: str) -> str:
    return (
        "CAST(\n"
        f"        OREPLACE(CAST({column_name} AS VARCHAR(32000)), {token}, {replacement})\n"
        "        AS CLOB(32000)\n"
        "    )"
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
        or sample_row.get("entity_metadata_id")
        or "metadata"
    )
    raw = f"{test_id}-{issue_code}-{row_id}"
    return "".join(char if char.isalnum() else "-" for char in str(raw)).strip("-").upper()


def _identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        msg = f"[ADPTrust.InvalidIdentifier] Unsafe SQL identifier {value}."
        raise ValueError(msg)
    return value


def _repair_database_name(database_name: str) -> str:
    if database_name.endswith("_STD_V"):
        return _identifier(database_name.removesuffix("_STD_V") + "_STD_T")
    return _identifier(database_name)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_literal(value: object) -> str:
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int | float):
        return str(value)
    return _sql_string(str(value))


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0
