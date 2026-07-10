"""Fail-fast behaviour when the database is unreachable.

A dead or overloaded connection is not one check's problem: continuing would
stamp the identical backend error onto every remaining check and publish a
misleading all-ERROR report. These tests pin the classifier, the fatal signal,
and the CLI exit path — while confirming a genuine per-check error (a missing
object) still records ERROR and lets the run continue.
"""

import pytest

from ai_native_data_product_trust_engine import cli
from ai_native_data_product_trust_engine.adapters import (
    DatabaseUnavailableError,
    LoggingAdapter,
)
from ai_native_data_product_trust_engine.error_formatting import is_database_unavailable_error
from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    TestCase,
    TestCategory,
    TestSeverity,
)
from ai_native_data_product_trust_engine.validators import run_test_case, run_validation

VIRTUAL_CIRCUITS_ERROR = (
    "(teradatasql.OperationalError) [Version 20.0.0.62] [Session 0] "
    "[Teradata Database] [Error 8024] [SQLState HY000] "
    "All virtual circuits are currently in use."
)


class UnavailableAdapter:
    def fetch_all(self, sql):
        raise RuntimeError(VIRTUAL_CIRCUITS_ERROR)

    def fetch_all_with_session_setup(self, sql, setup_sql=None, teardown_sql=None):
        raise RuntimeError(VIRTUAL_CIRCUITS_ERROR)

    def execute(self, sql):
        raise RuntimeError(VIRTUAL_CIRCUITS_ERROR)


class ObjectMissingAdapter:
    def fetch_all(self, sql):
        raise RuntimeError("[Teradata Database] [Error 3807] Object 'X.Y' does not exist.")

    def execute(self, sql):
        raise AssertionError("execute should not be called")


def _case(test_id: str = "CALLCENTRE-SEM-001") -> TestCase:
    return TestCase(
        test_id=test_id,
        name="check",
        category=TestCategory.SEMANTIC,
        severity=TestSeverity.CRITICAL,
        sql="SELECT 1 FROM CallCentre_SEM_STD_V.entity_metadata;",
        expected_result="Returns zero rows.",
        expected=ExpectedResult.ZERO_ROWS,
    )


def test_classifier_flags_connectivity_but_not_check_sql_errors():
    assert is_database_unavailable_error(VIRTUAL_CIRCUITS_ERROR)
    assert is_database_unavailable_error("[Error 8017] The UserId, Password or Account is invalid.")
    assert is_database_unavailable_error("Failed to connect to host td.example.com")
    assert is_database_unavailable_error("[Error 8018] connection refused")
    # A fault in one check's SQL is NOT an availability failure.
    assert not is_database_unavailable_error("[Error 3807] Object 'X.Y' does not exist.")
    assert not is_database_unavailable_error("[Error 3706] Syntax error near ';'.")
    assert not is_database_unavailable_error("no update permission")


def test_run_validation_stops_when_database_unavailable():
    adapter = LoggingAdapter(UnavailableAdapter())
    with pytest.raises(DatabaseUnavailableError) as excinfo:
        run_validation(
            "CallCentre",
            adapter,
            [_case(), _case("CALLCENTRE-SEM-002")],
            include_capability_scans=False,
            include_query_template_scans=False,
            include_relationship_health_scans=False,
            include_text_reference_scans=False,
            include_view_contract_scans=False,
        )
    assert "DatabaseUnavailable" in str(excinfo.value)
    # The fatal signal must NOT be an Exception, so the broad `except Exception`
    # blocks in run_test_case and the scanners cannot swallow it.
    assert not isinstance(excinfo.value, Exception)


def test_object_missing_still_records_error_and_does_not_abort():
    result = run_test_case(LoggingAdapter(ObjectMissingAdapter()), _case())
    assert result.status.value == "ERROR"
    assert result.sample_rows[0]["issue_code"] == "BACKEND_ERROR"


def test_cli_validate_returns_distinct_code_and_writes_no_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "adapter_from_environment", lambda url: UnavailableAdapter())
    report = tmp_path / "trust-report.json"

    code = cli.main(
        ["validate", "--prefix", "CallCentre", "--database-url", "teradatasql://x",
         "--output", str(report)]
    )

    assert code == 3
    assert "DatabaseUnavailable" in capsys.readouterr().err
    assert not report.exists()  # no partial, all-ERROR report is left behind
