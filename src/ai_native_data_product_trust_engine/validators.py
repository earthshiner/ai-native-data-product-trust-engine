"""Validation orchestration contracts.

The first implementation will keep database access behind an adapter so tests can be
unit-tested without a live Teradata connection.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Protocol

from ai_native_data_product_trust_engine.models import (
    ExcludedCheck,
    ExpectedResult,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.capabilities import run_capability_validations
from ai_native_data_product_trust_engine.query_templates import run_query_template_validations
from ai_native_data_product_trust_engine.relationship_health import (
    run_relationship_health_validations,
)
from ai_native_data_product_trust_engine.text_references import run_text_reference_validations
from ai_native_data_product_trust_engine.view_contracts import run_view_contract_validations


class DatabaseAdapter(Protocol):
    def fetch_all(self, sql: str) -> list[dict[str, object]]:
        """Run SQL and return rows as dictionaries."""

    def fetch_all_with_session_setup(
        self,
        sql: str,
        setup_sql: str | None = None,
        teardown_sql: str | None = None,
    ) -> list[dict[str, object]]:
        """Run SQL with optional setup/teardown statements on the same session."""

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
            sample_rows=[_backend_error_evidence(test_case)],
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
    enable_helpstats: bool = False,
    excluded_checks: list[ExcludedCheck] | None = None,
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
                enable_helpstats=enable_helpstats,
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
        excluded_checks=excluded_checks or [],
    )


def _status_from_rows(expected: ExpectedResult, row_count: int) -> TestStatus:
    if expected == ExpectedResult.ZERO_ROWS:
        return TestStatus.PASSED if row_count == 0 else TestStatus.FAILED
    if expected == ExpectedResult.NON_EMPTY:
        return TestStatus.PASSED if row_count > 0 else TestStatus.FAILED

    msg = f"[ADPTrust.UnsupportedExpectation] Unsupported expectation {expected}."
    raise ValueError(msg)


def _backend_error_evidence(test_case: TestCase) -> dict[str, object]:
    evidence = {
        "issue_code": "BACKEND_ERROR",
        "check_id": test_case.test_id,
        "check_name": test_case.name,
        "repair_hint": (
            test_case.repair_strategy
            or "Inspect the check SQL and referenced objects, then rerun validation."
        ),
    }
    if test_case.inspection_scope:
        evidence["inspection_scope"] = test_case.inspection_scope
    inspected_objects = _referenced_objects(test_case.sql)
    if inspected_objects:
        evidence["inspected_objects"] = inspected_objects
    return evidence


def _referenced_objects(sql: str) -> list[str]:
    matches = re.findall(
        r"\b(?:FROM|JOIN)\s+([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z][A-Za-z0-9_]*)",
        sql,
        flags=re.IGNORECASE,
    )
    objects = [
        f"{database_name}.{object_name}"
        for database_name, object_name in matches
        if database_name.upper() != "DBC"
    ]
    return list(dict.fromkeys(objects))


def _run_scanner(
    prefix: str,
    scanner_id: str,
    scanner_name: str,
    category: TestCategory,
    scanner,
    adapter: DatabaseAdapter,
    **scanner_kwargs,
) -> list[TestResult]:
    try:
        return scanner(prefix, adapter, **scanner_kwargs)
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
                    {
                        "issue_code": "SCANNER_BACKEND_ERROR",
                        "scanner": scanner_id,
                        "repair_hint": (
                            "A specialised validation scanner could not complete. Inspect the "
                            "backend error, fix the missing or invalid object, then rerun."
                        ),
                    }
                ],
                error_message=str(exc),
            )
        ]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
