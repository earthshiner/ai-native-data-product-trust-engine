import json

from ai_native_data_product_trust_engine.adapters import _teradatasql_args_from_url
from ai_native_data_product_trust_engine.cli import _summarise_error, main
from ai_native_data_product_trust_engine.models import ExpectedResult, TestCase, TestCategory, TestSeverity
from ai_native_data_product_trust_engine.repairs import classify_stale_relationship_path_name
from ai_native_data_product_trust_engine.test_generation import generate_metadata_tests
from ai_native_data_product_trust_engine.validators import run_test_case


def test_generate_metadata_tests_includes_core_contracts():
    tests = generate_metadata_tests("CallCentre")

    assert [test.test_id for test in tests] == [
        "CALLCENTRE-SEM-001",
        "CALLCENTRE-SEM-002",
        "CALLCENTRE-SEM-003",
        "CALLCENTRE-QUERY-001",
    ]
    assert all("CallCentre_" in test.sql for test in tests)
    assert tests[-1].expected == ExpectedResult.NON_EMPTY


def test_generate_tests_cli_includes_free_text_cases(capsys):
    exit_code = main(["generate-tests", "--prefix", "CallCentre"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CALLCENTRE-TEXT-004\tFREE_TEXT\tQuery cookbook free-text references are current" in (
        captured.out
    )


def test_classify_stale_relationship_path_name():
    candidate = classify_stale_relationship_path_name("v_relationship_patsh")

    assert candidate is not None
    assert candidate.issue_code == "STALE_OBJECT_NAME"
    assert candidate.requires_approval is False


def test_run_test_case_passes_zero_row_expectation():
    adapter = StubAdapter(rows=[])
    result = run_test_case(adapter, _test_case(ExpectedResult.ZERO_ROWS))

    assert result.status.value == "PASSED"
    assert result.row_count == 0


def test_run_test_case_fails_non_empty_expectation():
    adapter = StubAdapter(rows=[])
    result = run_test_case(adapter, _test_case(ExpectedResult.NON_EMPTY))

    assert result.status.value == "FAILED"
    assert result.row_count == 0


def test_validate_cli_writes_report(monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"

    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.adapter_from_environment",
        lambda database_url=None: StubAdapter(rows=[]),
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.generate_metadata_tests",
        lambda prefix: [_test_case(ExpectedResult.ZERO_ROWS)],
    )

    exit_code = main(["validate", "--prefix", "CallCentre", "--output", str(report_path)])

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["passed"] == 8


def test_teradatasql_args_from_url_redacts_nothing_but_parses_components():
    args = _teradatasql_args_from_url("teradata://user:p%40ss@example.com:1025/db?logmech=LDAP")

    assert args == {
        "host": "example.com",
        "user": "user",
        "password": "p@ss",
        "dbs_port": "1025",
        "database": "db",
        "logmech": "LDAP",
    }


def test_summarise_error_removes_driver_stack():
    assert _summarise_error("Teradata error.\n at driver stack") == "Teradata error."


class StubAdapter:
    def __init__(self, rows):
        self.rows = rows

    def fetch_all(self, sql):
        return self.rows


def _test_case(expected: ExpectedResult) -> TestCase:
    return TestCase(
        test_id="TEST-001",
        name="Example generated test",
        category=TestCategory.SEMANTIC,
        severity=TestSeverity.CRITICAL,
        sql="SELECT 1;",
        expected_result="Example expectation.",
        expected=expected,
    )
