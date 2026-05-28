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
    TestResult,
    TestStatus,
    ValidationRun,
)


class DatabaseAdapter(Protocol):
    def fetch_all(self, sql: str) -> list[dict[str, object]]:
        """Run SQL and return rows as dictionaries."""


def run_test_case(adapter: DatabaseAdapter, test_case: TestCase) -> TestResult:
    try:
        rows = adapter.fetch_all(test_case.sql)
    except Exception as exc:  # noqa: BLE001 - adapters normalise backend errors later.
        return TestResult(
            test_case=test_case,
            status=TestStatus.ERROR,
            row_count=0,
            error_message=str(exc),
        )

    status = _status_from_rows(test_case.expected, len(rows))
    return TestResult(
        test_case=test_case,
        status=status,
        row_count=len(rows),
        sample_rows=rows[:10],
    )


def run_validation(prefix: str, adapter: DatabaseAdapter, tests: list[TestCase]) -> ValidationRun:
    started_at = _utc_now()
    results = [run_test_case(adapter, test_case) for test_case in tests]
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
