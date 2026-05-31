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
from ai_native_data_product_trust_engine.reports import validation_run_to_dict
from ai_native_data_product_trust_engine.scoring import scorecards

TERADATA_DRIVER_STACK_ERROR = (
    "[Version 20.0.0.56] [Session 5405] [Teradata Database] [Error 3807] "
    "Object 'CallCentre_SEM_STD_T.data_product_registry' does not exist. "
    "at gosqldriver/teradatasql.MakeError ErrorUtil.go:100 at "
    "gosqldriver/teradatasql.formatError ErrorUtil.go:106 at "
    "database/sql.(*DB).queryDC sql.go:1781 at runtime.goexit asm_amd64.s:1771"
)


def test_write_html_report_creates_branded_interactive_report(tmp_path):
    output_path = tmp_path / "trust.html"
    run = ValidationRun(
        prefix="CallCentre",
        started_at="2026-05-29T00:00:00+00:00",
        completed_at="2026-05-29T00:00:01+00:00",
        results=[
            _result("CALLCENTRE-SEM-001", TestStatus.PASSED),
            _result(
                "CALLCENTRE-PERF-001",
                TestStatus.FAILED,
                category=TestCategory.PERFORMANCE,
            ),
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
    assert "Data product trust score" in html
    assert "Performance readiness score" in html
    assert "Operational readiness score" in html
    assert "Checks carried out" in html
    assert "Glossary" in html
    assert 'role="tablist"' in html
    assert 'aria-controls="panel-checks"' in html
    assert 'id="panel-checks"' in html
    assert 'id="tab-overview"' in html
    assert 'aria-controls="panel-results"' in html
    assert 'id="panel-results"' in html
    assert 'class="tab-panel"' in html
    assert "document.querySelectorAll(&quot;[role=&#x27;tab&#x27;]&quot;)" not in html
    assert "document.querySelectorAll(\"[role='tab']\")" in html
    assert "data:image/png;base64" in html
    assert "<span>N/A</span>" in html
    assert "title=\"Checks product meaning" in html
    assert "Free Text" in html
    assert "#FF5F02" in html
    assert "#00233C" in html
    assert "trust-report-data" in html
    assert "Duration" in html
    assert '<div class="metric-value">1s</div>' in html
    assert "CALLCENTRE-SEM-001" in html
    assert "Returns zero rows." in html
    assert "statusFilter" in html
    assert "MISSING_COLUMN" in html
    assert "Approval required" in html
    assert "Backend error" in html
    assert "Potential consequence" in html
    assert "Generated SQL, views or recipes that depend on this column may fail at runtime." in html
    assert (
        "Column/Parameter &#x27;CallCentre_DOM_STD_T.Call_H.start_ts&#x27; does not exist."
        in html
    )
    assert "ALTER TABLE CallCentre_DOM_STD_T.Call_H ADD start_ts &lt;data_type&gt;" in html
    assert "Recreate or test these dependent objects first" in html
    assert "CallCentre_DOM_BUS_V.Call_Enriched" in html
    assert "Root cause groups" in html
    assert "Missing column: CallCentre_DOM_STD_T.Call_H.start_ts" in html
    assert "2 downstream failures" in html
    assert ".root-cause-card p," in html
    assert "overflow-wrap: anywhere" in html


def test_html_report_never_displays_raw_backend_stack_as_evidence(tmp_path):
    output_path = tmp_path / "trust.html"
    run = ValidationRun(
        prefix="CallCentre",
        started_at="2026-05-29T00:00:00+00:00",
        completed_at="2026-05-29T00:00:01+00:00",
        results=[
            _result(
                "CALLCENTRE-DISCOVERY-002",
                TestStatus.ERROR,
                error_message=TERADATA_DRIVER_STACK_ERROR,
            )
        ],
    )

    write_html_report(run, output_path, [])

    html = output_path.read_text(encoding="utf-8")
    assert "Backend error" in html
    assert "Potential consequence" in html
    assert "Agents may misunderstand product meaning" in html
    assert "Object &#x27;CallCentre_SEM_STD_T.data_product_registry&#x27; does not exist." in html
    assert "gosqldriver" not in html
    assert "database/sql" not in html
    assert "runtime.goexit" not in html


def test_html_report_explains_relationship_datatype_mismatch_consequence(tmp_path):
    output_path = tmp_path / "trust.html"
    run = ValidationRun(
        prefix="CallCentre",
        started_at="2026-05-29T00:00:00+00:00",
        completed_at="2026-05-29T00:00:01+00:00",
        results=[
            _result(
                "CALLCENTRE-SEM-004",
                TestStatus.FAILED,
                sample_rows=[
                    {
                        "relationship_name": "Call to Customer",
                        "issue_code": "JOIN_COLUMN_TYPE_MISMATCH",
                    }
                ],
            )
        ],
    )

    write_html_report(run, output_path, [])

    html = output_path.read_text(encoding="utf-8")
    assert "Potential consequence" in html
    assert "Generated relationship joins may rely on implicit casts" in html


def test_html_report_explains_recipe_bounds_and_explain_consequences(tmp_path):
    output_path = tmp_path / "trust.html"
    run = ValidationRun(
        prefix="CallCentre",
        started_at="2026-05-29T00:00:00+00:00",
        completed_at="2026-05-29T00:00:01+00:00",
        results=[
            _result(
                "CALLCENTRE-QUERY-BOUNDS-QC-001",
                TestStatus.FAILED,
                category=TestCategory.PERFORMANCE,
                sample_rows=[{"issue_code": "UNBOUNDED_INTERACTIVE_RECIPE"}],
            ),
            _result(
                "CALLCENTRE-QUERY-EXPLAIN-PERF-QC-001",
                TestStatus.FAILED,
                category=TestCategory.PERFORMANCE,
                sample_rows=[{"issue_code": "EXPLAIN_PRODUCT_JOIN"}],
            ),
        ],
    )

    write_html_report(run, output_path, [])

    html = output_path.read_text(encoding="utf-8")
    assert "Agents may run open-ended queries over large tables" in html
    assert "causing excessive work or unexpected result expansion" in html


def test_scorecards_keep_trust_performance_and_operational_separate():
    results = [
        _result("CALLCENTRE-SEM-001", TestStatus.PASSED, category=TestCategory.SEMANTIC),
        _result(
            "CALLCENTRE-PERF-001",
            TestStatus.FAILED,
            category=TestCategory.PERFORMANCE,
        ),
        _result(
            "CALLCENTRE-OPS-001",
            TestStatus.ERROR,
            category=TestCategory.OPERATIONAL,
        ),
    ]

    scores = scorecards(results)

    assert scores["data_product_trust"]["score"] == 100
    assert scores["performance_readiness"]["score"] == 0
    assert scores["operational_readiness"]["score"] == 0


def test_json_report_includes_separate_score_families():
    run = ValidationRun(
        prefix="CallCentre",
        started_at="2026-05-29T00:00:00+00:00",
        completed_at="2026-05-29T00:00:01+00:00",
        results=[
            _result("CALLCENTRE-SEM-001", TestStatus.PASSED, category=TestCategory.SEMANTIC),
            _result(
                "CALLCENTRE-PERF-001",
                TestStatus.FAILED,
                category=TestCategory.PERFORMANCE,
            ),
        ],
    )

    report = validation_run_to_dict(run)

    assert report["scores"]["data_product_trust"]["score"] == 100
    assert report["scores"]["performance_readiness"]["score"] == 0
    assert report["scores"]["operational_readiness"]["assessed"] is False
    assert report["summary"]["duration_seconds"] == 1.0
    assert report["summary"]["duration"] == "1s"


def test_json_report_uses_friendly_backend_error_message():
    run = ValidationRun(
        prefix="CallCentre",
        started_at="2026-05-29T00:00:00+00:00",
        completed_at="2026-05-29T00:00:01+00:00",
        results=[
            _result(
                "CALLCENTRE-DISCOVERY-002",
                TestStatus.ERROR,
                error_message=TERADATA_DRIVER_STACK_ERROR,
            )
        ],
    )

    report = validation_run_to_dict(run)

    error_message = report["results"][0]["error_message"]
    assert error_message.endswith("does not exist.")
    assert "gosqldriver" not in error_message
    assert "database/sql" not in error_message


def _result(
    test_id: str,
    status: TestStatus,
    category: TestCategory = TestCategory.SEMANTIC,
    sample_rows: list[dict[str, object]] | None = None,
    error_message: str | None = None,
) -> TestResult:
    return TestResult(
        test_case=TestCase(
            test_id=test_id,
            name=f"Test {test_id}",
            category=category,
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
