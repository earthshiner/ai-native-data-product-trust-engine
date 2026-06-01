import json
from decimal import Decimal

from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.reports import write_json_report


def test_write_json_report_serialises_decimal_evidence(tmp_path):
    report_path = tmp_path / "report.json"
    run = ValidationRun(
        prefix="CallCentre",
        started_at="2026-06-01T10:00:00+10:00",
        completed_at="2026-06-01T10:00:01+10:00",
        results=[
            TestResult(
                test_case=TestCase(
                    test_id="CALLCENTRE-STRUCT-002",
                    name="Product tables stay within skew threshold",
                    category=TestCategory.STRUCTURAL,
                    severity=TestSeverity.WARNING,
                    sql="SELECT 1;",
                    expected_result="No high skew rows.",
                    expected=ExpectedResult.ZERO_ROWS,
                ),
                status=TestStatus.FAILED,
                row_count=1,
                sample_rows=[
                    {
                        "issue_code": "TABLE_SKEW_HIGH",
                        "skew_percent": Decimal("27.35"),
                        "current_perm": Decimal("12345678901234567890"),
                    }
                ],
            )
        ],
    )

    write_json_report(run, report_path)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    sample = payload["results"][0]["sample_rows"][0]
    assert sample["skew_percent"] == "27.35"
    assert sample["current_perm"] == "12345678901234567890"
