"""Publish compact Trust Engine evidence into data product tables."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Protocol

from ai_native_data_product_trust_engine.models import TestSeverity, TestStatus, ValidationRun
from ai_native_data_product_trust_engine.repairs import RepairCandidate
from ai_native_data_product_trust_engine.reports import validation_run_to_dict

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_PUBLISH_COLUMNS = (
    "product_prefix",
    "run_id",
    "started_dts",
    "completed_dts",
    "trust_status",
    "agent_use_allowed",
    "total_checks",
    "passed_count",
    "failed_count",
    "error_count",
    "critical_failure_count",
    "error_failure_count",
    "data_product_trust_score",
    "performance_readiness_score",
    "operational_readiness_score",
    "repair_candidate_count",
    "failed_checks_json",
    "repair_candidates_json",
)


class PublishAdapter(Protocol):
    def execute(self, sql: str) -> None:
        """Execute a non-query SQL statement."""


def default_trust_table(prefix: str) -> str:
    return f"{prefix}_SEM_STD_T.trust_engine_run"


def default_trust_view(prefix: str) -> str:
    return f"{prefix}_SEM_BUS_V.trust_engine_latest"


def trust_table_ddl(prefix: str, table_name: str | None = None) -> str:
    qualified_table = _qualified_identifier(table_name or default_trust_table(prefix))
    return f"""CREATE MULTISET TABLE {qualified_table}
(
    product_prefix VARCHAR(128) CHARACTER SET LATIN NOT NULL,
    run_id VARCHAR(64) CHARACTER SET LATIN NOT NULL,
    started_dts TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    completed_dts TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    trust_status VARCHAR(16) CHARACTER SET LATIN NOT NULL,
    agent_use_allowed BYTEINT NOT NULL,
    total_checks INTEGER NOT NULL,
    passed_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    error_count INTEGER NOT NULL,
    critical_failure_count INTEGER NOT NULL,
    error_failure_count INTEGER NOT NULL,
    data_product_trust_score INTEGER,
    performance_readiness_score INTEGER,
    operational_readiness_score INTEGER,
    repair_candidate_count INTEGER NOT NULL,
    failed_checks_json JSON(32000) CHARACTER SET UNICODE,
    repair_candidates_json JSON(32000) CHARACTER SET UNICODE
)
PRIMARY INDEX (product_prefix, completed_dts);"""


def trust_latest_view_ddl(
    prefix: str,
    table_name: str | None = None,
    view_name: str | None = None,
) -> str:
    qualified_table = _qualified_identifier(table_name or default_trust_table(prefix))
    qualified_view = _qualified_identifier(view_name or default_trust_view(prefix))
    columns = ",\n    ".join(_PUBLISH_COLUMNS)
    return f"""CREATE VIEW {qualified_view}
(
    {columns}
)
AS
LOCKING ROW FOR ACCESS
SELECT
    product_prefix,
    run_id,
    started_dts,
    completed_dts,
    trust_status,
    agent_use_allowed,
    total_checks,
    passed_count,
    failed_count,
    error_count,
    critical_failure_count,
    error_failure_count,
    data_product_trust_score,
    performance_readiness_score,
    operational_readiness_score,
    repair_candidate_count,
    failed_checks_json,
    repair_candidates_json
FROM {qualified_table}
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY product_prefix
    ORDER BY completed_dts DESC, run_id DESC
) = 1;"""


def publish_trust_result(
    adapter: PublishAdapter,
    run: ValidationRun,
    repair_candidates: list[RepairCandidate],
    table_name: str | None = None,
) -> str:
    qualified_table = _qualified_identifier(table_name or default_trust_table(run.prefix))
    sql = trust_result_insert_sql(run, repair_candidates, qualified_table)
    adapter.execute(sql)
    return qualified_table


def trust_result_insert_sql(
    run: ValidationRun,
    repair_candidates: list[RepairCandidate],
    table_name: str | None = None,
) -> str:
    qualified_table = _qualified_identifier(table_name or default_trust_table(run.prefix))
    row = _publish_row(run, repair_candidates)
    columns = ", ".join(_PUBLISH_COLUMNS)
    values = ", ".join(_sql_value(column, row[column]) for column in _PUBLISH_COLUMNS)
    return f"INSERT INTO {qualified_table} ({columns}) VALUES ({values});"


def _publish_row(
    run: ValidationRun,
    repair_candidates: list[RepairCandidate],
) -> dict[str, object | None]:
    report = validation_run_to_dict(run)
    scores = report["scores"]
    failed_results = [
        result for result in run.results if result.status in {TestStatus.FAILED, TestStatus.ERROR}
    ]
    critical_failure_count = sum(
        1 for result in failed_results if result.test_case.severity == TestSeverity.CRITICAL
    )
    error_failure_count = sum(
        1 for result in failed_results if result.test_case.severity == TestSeverity.ERROR
    )
    trust_status = _trust_status(report, critical_failure_count, error_failure_count)
    run_id = _run_id(run)
    return {
        "product_prefix": run.prefix,
        "run_id": run_id,
        # Wire values stay canonical ISO-8601 strings in the row dict (the JSON
        # fixture has no timestamp type); the SQL layer binds them as typed
        # TIMESTAMP WITH TIME ZONE literals.
        "started_dts": run.started_at,
        "completed_dts": run.completed_at,
        "trust_status": trust_status,
        "agent_use_allowed": 1 if trust_status in {"TRUSTED", "DEGRADED"} else 0,
        "total_checks": report["summary"]["total"],
        "passed_count": report["summary"]["passed"],
        "failed_count": report["summary"]["failed"],
        "error_count": report["summary"]["errors"],
        "critical_failure_count": critical_failure_count,
        "error_failure_count": error_failure_count,
        "data_product_trust_score": _score_value(scores, "data_product_trust"),
        "performance_readiness_score": _score_value(scores, "performance_readiness"),
        "operational_readiness_score": _score_value(scores, "operational_readiness"),
        "repair_candidate_count": len(repair_candidates),
        "failed_checks_json": _failed_checks_json(report),
        "repair_candidates_json": _repair_candidates_json(repair_candidates),
    }


def _trust_status(
    report: dict[str, object],
    critical_failure_count: int,
    error_failure_count: int,
) -> str:
    summary = report["summary"]
    data_product_trust_score = _score_value(report["scores"], "data_product_trust")
    if summary["errors"] or critical_failure_count or error_failure_count:
        return "UNTRUSTED"
    if data_product_trust_score is not None and data_product_trust_score < 70:
        return "UNTRUSTED"
    if summary["failed"] or (data_product_trust_score is not None and data_product_trust_score < 90):
        return "DEGRADED"
    return "TRUSTED"


def _failed_checks_json(report: dict[str, object]) -> str:
    failed_checks = []
    for result in report["results"]:
        if result["status"] not in {"FAILED", "ERROR"}:
            continue
        test_case = result["test_case"]
        failed_checks.append(
            {
                "test_id": test_case["test_id"],
                "name": test_case["name"],
                "category": test_case["category"],
                "severity": test_case["severity"],
                "status": result["status"],
                "row_count": result["row_count"],
                "sample_rows": result["sample_rows"][:3],
                "error_message": result.get("error_message"),
                "repair_strategy": test_case.get("repair_strategy"),
            }
        )
    return _json_text(failed_checks[:20])


def _repair_candidates_json(repair_candidates: list[RepairCandidate]) -> str:
    return _json_text(
        [
            {
                "candidate_id": candidate.candidate_id,
                "issue_code": candidate.issue_code,
                "summary": candidate.summary,
                "mode": candidate.mode.value,
                "requires_approval": candidate.requires_approval,
                "sql": candidate.sql,
            }
            for candidate in repair_candidates[:20]
        ]
    )


def _json_text(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return payload[:32000]


def _run_id(run: ValidationRun) -> str:
    # Hashes the canonical ISO-8601 strings, not any database rendering, so the
    # identifier stays deterministic across wire schema changes.
    payload = f"{run.prefix}|{run.started_at}|{run.completed_at}|{len(run.results)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _score_value(scores: object, key: str) -> int | None:
    if not isinstance(scores, dict):
        return None
    scorecard = scores.get(key, {})
    if not isinstance(scorecard, dict):
        return None
    value = scorecard.get("score")
    return value if isinstance(value, int) else None


def _sql_value(column_name: str, value: object | None) -> str:
    if column_name in {"failed_checks_json", "repair_candidates_json"}:
        return f"CAST({_sql_literal(value)} AS JSON)"
    if column_name in {"started_dts", "completed_dts"}:
        return _timestamp_literal(value)
    return _sql_literal(value)


def _timestamp_literal(value: object | None) -> str:
    """Render an ISO-8601 instant as a Teradata TIMESTAMP WITH TIME ZONE literal.

    Accepts the canonical ``2026-01-01T00:05:00+00:00`` form (any offset) and
    the ``Z`` suffix; the ``T`` separator becomes a space for the literal.
    """
    if value is None:
        msg = (
            "[ADPTrust.InvalidTrustTimestamp] Run timestamps are required for publishing. "
            "Suggested action: ensure the validation run records started/completed instants."
        )
        raise ValueError(msg)
    text = str(value).strip().replace("T", " ", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?[+-]\d{2}:\d{2}", text
    ):
        msg = (
            f"[ADPTrust.InvalidTrustTimestamp] Not an ISO-8601 instant with offset: {value!r}. "
            "Suggested action: publish timestamps like '2026-01-01T00:05:00+00:00'."
        )
        raise ValueError(msg)
    return f"TIMESTAMP '{text}'"


def _sql_literal(value: object | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    escaped_value = str(value).replace("'", "''")
    return f"'{escaped_value}'"


def _qualified_identifier(value: str) -> str:
    parts = value.split(".")
    if len(parts) != 2 or not all(_IDENTIFIER.fullmatch(part) for part in parts):
        msg = (
            f"[ADPTrust.InvalidTrustTable] Invalid trust table or view name {value}. "
            "Suggested action: use a two-part Teradata name such as "
            "{ProductPrefix}_SEM_STD_T.trust_engine_run."
        )
        raise ValueError(msg)
    return value
