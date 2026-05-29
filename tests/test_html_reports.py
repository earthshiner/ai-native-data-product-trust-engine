from ai_native_data_product_trust_engine.html_reports import write_html_report
from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    RepairMode,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.repairs import RepairCandidate


def test_write_html_report_creates_branded_interactive_report(tmp_path):
    output_path = tmp_path / "trust.html"
    run = ValidationRun(
        prefix="CallCentre",
        started_at="2026-05-29T00:00:00+00:00",
        completed_at="2026-05-29T00:00:01+00:00",
        results=[
            _result("CALLCENTRE-SEM-001", TestStatus.PASSED),
            _result(
                "CALLCENTRE-QUERY-EXPLAIN-QC-001",
                TestStatus.FAILED,
                sample_rows=[
                    {
                        "issue_code": "MISSING_COLUMN",
                        "missing_column": "CallCentre_DOM_STD_T.Call_H.start_ts",
                        "repair_hint": "Refresh the view contract.",
                    }
                ],
                error_message=(
                    "[Version 20.0.0.56] [Session 2822] [Teradata Database] [Error 3810] "
                    "Column/Parameter 'CallCentre_DOM_STD_T.Call_H.start_ts' does not exist. "
                    "at gosqldriver/teradatasql.MakeError ErrorUtil.go:100"
                ),
            ),
            _result(
                "CALLCENTRE-VIEW-COLUMNS-CallCentre_DOM_BUS_V.Call_Enriched",
                TestStatus.FAILED,
                sample_rows=[
                    {
                        "issue_code": "MISSING_COLUMN",
                        "missing_column": "CallCentre_DOM_STD_T.Call_H.start_ts",
                        "database_name": "CallCentre_DOM_BUS_V",
                        "view_name": "Call_Enriched",
                    }
                ],
            ),
        ],
    )
    repairs = [
        RepairCandidate(
            candidate_id="REPAIR-001",
            issue_code="MISSING_COLUMN",
            summary="Repair a stale column reference.",
            mode=RepairMode.PROPOSAL,
            requires_approval=True,
        )
    ]

    write_html_report(run, output_path, repairs)

    html = output_path.read_text(encoding="utf-8")
    assert "CallCentre trust report" in html
    assert "#FF5F02" in html
    assert "#00233C" in html
    assert "trust-report-data" in html
    assert "statusFilter" in html
    assert "MISSING_COLUMN" in html
    assert "Approval required" in html
    assert "Backend error" in html
    assert (
        "Column/Parameter &#x27;CallCentre_DOM_STD_T.Call_H.start_ts&#x27; does not exist."
        in html
    )
    assert "ALTER TABLE CallCentre_DOM_STD_T.Call_H ADD start_ts &lt;data_type&gt;" in html
    assert "Recreate or test these dependent objects first" in html
    assert "CallCentre_DOM_BUS_V.Call_Enriched" in html


def _result(
    test_id: str,
    status: TestStatus,
    sample_rows: list[dict[str, object]] | None = None,
    error_message: str | None = None,
) -> TestResult:
    return TestResult(
        test_case=TestCase(
            test_id=test_id,
            name=f"Test {test_id}",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql="SELECT 1;",
            expected_result="Returns zero rows.",
            expected=ExpectedResult.ZERO_ROWS,
        ),
        status=status,
        row_count=0 if status == TestStatus.PASSED else 1,
        sample_rows=sample_rows or [],
        error_message=error_message,
    )
