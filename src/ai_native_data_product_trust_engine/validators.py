"""Validation orchestration contracts.

The first implementation will keep database access behind an adapter so tests can be
unit-tested without a live Teradata connection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.capabilities import run_capability_validations
from ai_native_data_product_trust_engine.error_formatting import concise_backend_error
from ai_native_data_product_trust_engine.query_templates import (
    extract_sql_error_evidence,
    run_query_template_validations,
)
from ai_native_data_product_trust_engine.relationship_health import (
    run_relationship_health_validations,
)
from ai_native_data_product_trust_engine.text_references import run_text_reference_validations
from ai_native_data_product_trust_engine.view_contracts import run_view_contract_validations


class DatabaseAdapter(Protocol):
    def fetch_all(self, sql: str) -> list[dict[str, object]]:
        """Run SQL and return rows as dictionaries."""

    def execute(self, sql: str) -> None:
        """Execute a non-query SQL statement."""


def run_test_case(adapter: DatabaseAdapter, test_case: TestCase) -> TestResult:
    try:
        rows = adapter.fetch_all(test_case.sql)
    except Exception as exc:  # noqa: BLE001 - adapters normalise backend errors later.
        return TestResult(
            test_case=test_case,
            status=TestStatus.ERROR,
            row_count=0,
            sample_rows=[_backend_error_evidence(test_case, exc)],
            error_message=str(exc),
        )

    status = _status_from_rows(test_case.expected, len(rows))
    return TestResult(
        test_case=test_case,
        status=status,
        row_count=len(rows),
        sample_rows=rows[:10],
    )


def run_validation(
    prefix: str,
    adapter: DatabaseAdapter,
    tests: list[TestCase],
    include_capability_scans: bool = True,
    include_query_template_scans: bool = True,
    include_relationship_health_scans: bool = True,
    include_text_reference_scans: bool = True,
    include_view_contract_scans: bool = True,
) -> ValidationRun:
    started_at = _utc_now()
    results = [run_test_case(adapter, test_case) for test_case in tests]
    if include_capability_scans:
        results.extend(
            _run_scanner(
                prefix,
                "CAPABILITY-SCAN",
                "Capability discovery scans complete",
                TestCategory.CAPABILITY,
                run_capability_validations,
                adapter,
            )
        )
    if include_query_template_scans:
        results.extend(
            _run_scanner(
                prefix,
                "QUERY-SCAN",
                "Query template scans complete",
                TestCategory.QUERY,
                run_query_template_validations,
                adapter,
            )
        )
    if include_relationship_health_scans:
        results.extend(
            _run_scanner(
                prefix,
                "RELATIONSHIP-SCAN",
                "Relationship health scans complete",
                TestCategory.DATA_QUALITY,
                run_relationship_health_validations,
                adapter,
            )
        )
    if include_text_reference_scans:
        results.extend(
            _run_scanner(
                prefix,
                "TEXT-SCAN",
                "Free-text reference scans complete",
                TestCategory.FREE_TEXT,
                run_text_reference_validations,
                adapter,
            )
        )
    if include_view_contract_scans:
        results.extend(
            _run_scanner(
                prefix,
                "VIEW-SCAN",
                "View contract scans complete",
                TestCategory.STRUCTURAL,
                run_view_contract_validations,
                adapter,
            )
        )
    completed_at = _utc_now()
    return ValidationRun(
        prefix=prefix,
        started_at=started_at,
        completed_at=completed_at,
        results=results,
    )


def _status_from_rows(expected: ExpectedResult, row_count: int) -> TestStatus:
    if expected == ExpectedResult.ZERO_ROWS:
        return TestStatus.PASSED if row_count == 0 else TestStatus.FAILED
    if expected == ExpectedResult.NON_EMPTY:
        return TestStatus.PASSED if row_count > 0 else TestStatus.FAILED

    msg = f"[ADPTrust.UnsupportedExpectation] Unsupported expectation {expected}."
    raise ValueError(msg)


def _run_scanner(
    prefix: str,
    scanner_id: str,
    scanner_name: str,
    category: TestCategory,
    scanner,
    adapter: DatabaseAdapter,
) -> list[TestResult]:
    try:
        return scanner(prefix, adapter)
    except Exception as exc:  # noqa: BLE001 - scanner failures are validation evidence.
        test_case = TestCase(
            test_id=f"{prefix.upper()}-{scanner_id}",
            name=scanner_name,
            category=category,
            severity=TestSeverity.CRITICAL,
            sql="",
            expected_result="Scanner completes and records validation evidence.",
            expected=ExpectedResult.ZERO_ROWS,
            repair_strategy=(
                "Review the missing object or backend error, repair the deployed metadata or "
                "view contract, then rerun validation."
            ),
        )
        return [
            TestResult(
                test_case=test_case,
                status=TestStatus.ERROR,
                row_count=0,
                sample_rows=[
                    _scanner_error_evidence(
                        scanner_id,
                        scanner_name,
                        test_case.test_id,
                        category,
                        exc,
                    )
                ],
                error_message=str(exc),
            )
        ]


def _backend_error_evidence(test_case: TestCase, exc: Exception) -> dict[str, object]:
    evidence = extract_sql_error_evidence(str(exc), test_case.sql)
    evidence.update(
        {
            "test_id": test_case.test_id,
            "test_name": test_case.name,
            "category": test_case.category.value,
            "source_type": "validation_check",
            "referenced_from": f"{test_case.test_id}: {test_case.name}",
            "backend_error": concise_backend_error(str(exc)),
        }
    )
    inspection_scope = _inspection_scope(test_case)
    if inspection_scope:
        evidence["objects_to_examine"] = inspection_scope
    return evidence


def _scanner_error_evidence(
    scanner_id: str,
    scanner_name: str,
    test_id: str,
    category: TestCategory,
    exc: Exception,
) -> dict[str, object]:
    evidence = extract_sql_error_evidence(str(exc))
    evidence.update(
        {
            "scanner": scanner_id,
            "test_id": test_id,
            "test_name": scanner_name,
            "category": category.value,
            "source_type": "specialised_scanner",
            "referenced_from": f"{test_id}: {scanner_name}",
            "backend_error": concise_backend_error(str(exc)),
        }
    )
    return evidence


def _inspection_scope(test_case: TestCase) -> list[str]:
    test_id = test_case.test_id.upper()
    prefix = test_id.split("-", 1)[0]
    if test_id.endswith("STRUCT-003"):
        return [
            f"{prefix}_% product tables in DBC.TablesV",
            "Primary index metadata in DBC.IndicesV",
            "Column nullability in DBC.ColumnsV",
            "AMP storage distribution in DBC.TableSizeV",
        ]
    if test_id.endswith("OPS-002"):
        return [
            f"Observability database registered in {prefix}_SEM_STD_V.data_product_map",
            "Observability tables: change_event, data_quality_metric, data_lineage, lineage_run",
            f"Semantic observability views in {prefix}_SEM_STD_V: lineage_graph, lineage_run_latest",
        ]
    return _referenced_sql_objects(test_case.sql)


def _referenced_sql_objects(sql: str) -> list[str]:
    object_refs: list[str] = []
    tokens = sql.replace("\n", " ").split()
    for index, token in enumerate(tokens[:-1]):
        if token.upper() not in {"FROM", "JOIN", "UPDATE", "INTO"}:
            continue
        candidate = tokens[index + 1].strip("(),;")
        if "." not in candidate or candidate.upper().startswith("SELECT"):
            continue
        object_refs.append(candidate)
    return list(dict.fromkeys(object_refs))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
