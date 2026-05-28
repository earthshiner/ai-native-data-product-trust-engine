from ai_native_data_product_trust_engine.models import (
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.repairs import (
    apply_safe_repairs,
    generate_repair_candidates,
    write_repair_reports,
)


def test_generate_repair_candidates_creates_safe_text_update():
    run = _run_with_sample(
        {
            "classification": "STALE_ALIAS",
            "database_name": "CallCentre_MEM_STD_V",
            "table_name": "Query_Cookbook",
            "column_name": "recipe_description",
            "row_key": "recipe_id=QC-DOMAIN-002",
            "key_values": {"recipe_id": "QC-DOMAIN-002"},
            "token": "v_relationship_paths",
            "replacement": "relationship_paths",
            "safe_auto_apply": True,
        }
    )

    candidates = generate_repair_candidates(run)

    assert len(candidates) == 1
    assert candidates[0].requires_approval is False
    assert "UPDATE CallCentre_MEM_STD_T.Query_Cookbook" in candidates[0].sql
    assert "SET is_active = 0" in candidates[0].sql
    assert "INSERT INTO CallCentre_MEM_STD_T.Query_Cookbook" in candidates[0].sql
    assert "OREPLACE(CAST(recipe_description AS VARCHAR(32000))" in candidates[0].sql
    assert "CURRENT_DATE AS valid_from" in candidates[0].sql


def test_write_repair_reports_outputs_safe_sql(tmp_path):
    candidates = generate_repair_candidates(
        _run_with_sample(
            {
                "database_name": "db",
                "table_name": "tab",
                "column_name": "txt",
                "row_key": "id=1",
                "key_values": {"id": 1},
                "token": "v_relationship_patsh",
                "replacement": "relationship_paths",
                "safe_auto_apply": True,
            }
        )
    )

    markdown_path, sql_path = write_repair_reports(candidates, tmp_path / "report.json")

    assert markdown_path.exists()
    assert "Repair Candidates" in markdown_path.read_text(encoding="utf-8")
    assert "UPDATE db.tab" in sql_path.read_text(encoding="utf-8")


def test_apply_safe_repairs_only_executes_safe_auto_candidates():
    candidates = generate_repair_candidates(
        _run_with_sample(
            {
                "database_name": "db",
                "table_name": "tab",
                "column_name": "txt",
                "row_key": "id=1",
                "key_values": {"id": 1},
                "token": "v_relationship_paths",
                "replacement": "relationship_paths",
                "safe_auto_apply": True,
            }
        )
    )
    adapter = StubAdapter()

    applied = apply_safe_repairs(adapter, candidates)

    assert [result.candidate for result in applied] == candidates
    assert all(result.applied for result in applied)
    assert len(adapter.executed_sql) == 1


def test_apply_safe_repairs_reports_execution_failures():
    candidates = generate_repair_candidates(
        _run_with_sample(
            {
                "database_name": "db",
                "table_name": "tab",
                "column_name": "txt",
                "row_key": "id=1",
                "key_values": {"id": 1},
                "token": "v_relationship_paths",
                "replacement": "relationship_paths",
                "safe_auto_apply": True,
            }
        )
    )
    adapter = StubAdapter(error=RuntimeError("no update permission"))

    applied = apply_safe_repairs(adapter, candidates)

    assert applied[0].applied is False
    assert applied[0].error_message == "no update permission"


class StubAdapter:
    def __init__(self, error=None):
        self.executed_sql = []
        self.error = error

    def execute(self, sql):
        if self.error:
            raise self.error
        self.executed_sql.append(sql)


def _run_with_sample(sample):
    return ValidationRun(
        prefix="CallCentre",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:00+00:00",
        results=[
            TestResult(
                test_case=TestCase(
                    test_id="CALLCENTRE-TEXT-004",
                    name="Query cookbook free-text references are current",
                    category=TestCategory.FREE_TEXT,
                    severity=TestSeverity.WARNING,
                    sql="SELECT 1;",
                    expected_result="Returns zero rows.",
                ),
                status=TestStatus.FAILED,
                row_count=1,
                sample_rows=[sample],
            )
        ],
    )
