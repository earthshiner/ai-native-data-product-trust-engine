"""A layout gap is reported but does not fail the run (Platform Layout Standard, VAL-20)."""

from ai_native_data_product_trust_engine import cli
from ai_native_data_product_trust_engine.models import (
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)


class QuietAdapter:
    def fetch_all(self, sql):
        return []

    def execute(self, sql):
        return None

    def close(self):
        return None


def _failed(test_id: str, severity: TestSeverity) -> TestResult:
    case = TestCase(
        test_id=test_id,
        name=test_id,
        category=TestCategory.SEMANTIC,
        severity=severity,
        sql="",
        expected_result="",
    )
    return TestResult(test_case=case, status=TestStatus.FAILED, row_count=1)


def _exit_code(monkeypatch, tmp_path, results):
    run = ValidationRun(prefix="ExampleProduct", started_at="t0", completed_at="t1", results=results)
    monkeypatch.setattr(cli, "adapter_from_environment", lambda url: QuietAdapter())
    monkeypatch.setattr(cli, "run_validation", lambda *args, **kwargs: run)
    return cli.main(
        ["validate", "--prefix", "ExampleProduct", "--database-url", "teradatasql://x",
         "--output", str(tmp_path / "report.json")]
    )


def test_only_a_layout_gap_does_not_fail_the_run(monkeypatch, tmp_path):
    results = [_failed("EXAMPLEPRODUCT-LAYOUT-001", TestSeverity.INFO)]
    assert _exit_code(monkeypatch, tmp_path, results) == 0


def test_a_layout_gap_does_not_hide_a_real_failure(monkeypatch, tmp_path):
    results = [
        _failed("EXAMPLEPRODUCT-LAYOUT-001", TestSeverity.INFO),
        _failed("EXAMPLEPRODUCT-SEM-001", TestSeverity.CRITICAL),
    ]
    assert _exit_code(monkeypatch, tmp_path, results) == 1
