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
    assert "\n   ,is_batch\n   ,module_version" in candidates[0].sql
    assert "CURRENT_DATE AS valid_from" in candidates[0].sql


def test_query_cookbook_temporal_repair_can_replace_sql_template_text():
    run = _run_with_sample(
        {
            "classification": "STALE_ALIAS",
            "database_name": "CallCentre_MEM_STD_V",
            "table_name": "Query_Cookbook",
            "column_name": "sql_template",
            "row_key": "recipe_id=QCB-CC-001",
            "key_values": {"recipe_id": "QCB-CC-001"},
            "token": "ch.valid_from_dts",
            "replacement": "ch.start_ts",
            "safe_auto_apply": True,
        }
    )

    candidate = generate_repair_candidates(run)[0]

    assert "OREPLACE(CAST(sql_template AS VARCHAR(32000))" in candidate.sql
    assert "AS sql_template" in candidate.sql


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


def test_entity_view_name_missing_with_deployed_view_is_safe_auto():
    run = _run_with_sample(
        {
            "issue_code": "ENTITY_VIEW_NAME_MISSING",
            "entity_metadata_id": 200001,
            "entity_name": "AgentSession",
            "business_database_name": "CallCentre_MEM_BUS_V",
            "view_name": None,
            "metadata_database_name": "CallCentre_SEM_STD_T",
            "metadata_table_name": "entity_metadata",
            "derived_view_name": "CallCentre_MEM_BUS_V.agent_session",
            "derived_view_deployed": "1",  # string form must be tolerated
        }
    )

    candidate = generate_repair_candidates(run)[0]

    assert candidate.requires_approval is False
    assert candidate.mode.value == "safe-auto"
    assert candidate.sql == (
        "UPDATE CallCentre_SEM_STD_T.entity_metadata\n"
        "SET view_name = 'CallCentre_MEM_BUS_V.agent_session'\n"
        "WHERE entity_metadata_id = 200001\n"
        "  AND view_name IS NULL;"
    )


def test_entity_view_name_missing_without_deployed_view_is_proposal():
    run = _run_with_sample(
        {
            "issue_code": "ENTITY_VIEW_NAME_MISSING",
            "entity_metadata_id": 300008,
            "entity_name": "ViewColumnType",
            "view_name": None,
            "metadata_database_name": "CallCentre_SEM_STD_T",
            "metadata_table_name": "entity_metadata",
            "derived_view_name": "CallCentre_SEM_BUS_V.view_column_type",
            "derived_view_deployed": 0,
        }
    )

    candidate = generate_repair_candidates(run)[0]

    assert candidate.requires_approval is True
    assert candidate.mode.value == "proposal"
    assert candidate.sql.startswith("-- PROPOSAL (ViewColumnType)")
    assert "WHERE entity_metadata_id = 300008" in candidate.sql
    # A proposal must never be auto-applied.
    adapter = StubAdapter()
    apply_safe_repairs(adapter, [candidate])
    assert adapter.executed_sql == []


def test_entity_view_name_not_deployed_is_deploy_proposal():
    run = _run_with_sample(
        {
            "issue_code": "ENTITY_VIEW_NAME_NOT_DEPLOYED",
            "entity_metadata_id": 2,
            "entity_name": "Call",
            "view_name": "CallCentre_DOM_BUS_V.Call_Current",
            "metadata_database_name": "CallCentre_SEM_STD_T",
            "metadata_table_name": "entity_metadata",
            "derived_view_name": "CallCentre_DOM_BUS_V.Call_Current",
            "derived_view_deployed": 1,
        }
    )

    candidate = generate_repair_candidates(run)[0]

    assert candidate.requires_approval is True
    assert candidate.mode.value == "proposal"
    assert "CallCentre_DOM_BUS_V.Call_Current" in candidate.sql
    assert "deploy the referenced view" in candidate.summary.lower()


def test_entity_view_name_safe_auto_is_written_and_applied(tmp_path):
    run = _run_with_sample(
        {
            "issue_code": "ENTITY_VIEW_NAME_MISSING",
            "entity_metadata_id": 100001,
            "entity_name": "AgentInteraction",
            "view_name": None,
            "metadata_database_name": "CallCentre_SEM_STD_T",
            "metadata_table_name": "entity_metadata",
            "derived_view_name": "CallCentre_MEM_BUS_V.agent_interaction",
            "derived_view_deployed": 1,
        }
    )
    candidates = generate_repair_candidates(run)

    _markdown_path, sql_path = write_repair_reports(candidates, tmp_path / "report.json")
    assert "UPDATE CallCentre_SEM_STD_T.entity_metadata" in sql_path.read_text(encoding="utf-8")

    adapter = StubAdapter()
    applied = apply_safe_repairs(adapter, candidates)
    assert applied[0].applied is True
    assert len(adapter.executed_sql) == 1


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
