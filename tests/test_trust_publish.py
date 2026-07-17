import pytest

from ai_native_data_product_trust_engine.cli import main
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
from ai_native_data_product_trust_engine.trust_publish import (
    default_trust_table,
    default_trust_view,
    publish_trust_result,
    trust_latest_view_ddl,
    trust_result_insert_sql,
    trust_table_ddl,
)


def test_trust_publish_defaults_to_semantic_standard_table_and_business_view():
    assert default_trust_table("CallCentre") == "CallCentre_SEM_STD_T.trust_engine_run"
    assert default_trust_view("CallCentre") == "CallCentre_SEM_BUS_V.trust_engine_latest"


def test_trust_table_ddl_defines_compact_agent_evidence_table():
    ddl = trust_table_ddl("CallCentre")

    assert ddl.startswith("CREATE MULTISET TABLE CallCentre_SEM_STD_T.trust_engine_run")
    assert "trust_status VARCHAR(16)" in ddl
    assert "agent_use_allowed BYTEINT NOT NULL" in ddl
    assert "failed_checks_json JSON(32000) CHARACTER SET UNICODE" in ddl
    assert "repair_candidates_json JSON(32000) CHARACTER SET UNICODE" in ddl
    assert "PRIMARY INDEX (product_prefix, completed_at)" in ddl


def test_trust_latest_view_ddl_uses_bus_v_latest_row_contract():
    ddl = trust_latest_view_ddl("CallCentre")

    assert ddl.startswith("CREATE VIEW CallCentre_SEM_BUS_V.trust_engine_latest")
    assert "LOCKING ROW FOR ACCESS" in ddl
    assert "FROM CallCentre_SEM_STD_T.trust_engine_run" in ddl
    assert "QUALIFY ROW_NUMBER() OVER" in ddl
    assert "ORDER BY completed_at DESC, run_id DESC" in ddl


def test_trust_result_insert_sql_summarises_failed_checks_and_repairs():
    run = _run(
        [
            _result("CALLCENTRE-SEM-001", TestStatus.PASSED),
            _result(
                "CALLCENTRE-SEM-002",
                TestStatus.FAILED,
                severity=TestSeverity.WARNING,
                sample_rows=[{"issue_code": "STALE_METADATA", "object_name": "Bad'Name"}],
            ),
        ]
    )
    repairs = [
        RepairCandidate(
            candidate_id="REPAIR-001",
            issue_code="STALE_METADATA",
            summary="Refresh stale metadata.",
            mode=RepairMode.PROPOSAL,
            requires_approval=True,
            sql="UPDATE x SET y = 'z';",
        )
    ]

    sql = trust_result_insert_sql(run, repairs)

    assert sql.startswith("INSERT INTO CallCentre_SEM_STD_T.trust_engine_run")
    assert "'UNTRUSTED'" in sql
    assert "CALLCENTRE-SEM-002" in sql
    assert "Bad''Name" in sql
    assert "REPAIR-001" in sql
    assert "UPDATE x SET y = ''z'';" in sql
    assert sql.count(" AS JSON)") == 2


def test_publish_trust_result_executes_insert_sql():
    adapter = _RecordingAdapter()
    run = _run([_result("CALLCENTRE-SEM-001", TestStatus.PASSED)])

    table_name = publish_trust_result(adapter, run, [])

    assert table_name == "CallCentre_SEM_STD_T.trust_engine_run"
    assert len(adapter.sql) == 1
    assert "INSERT INTO CallCentre_SEM_STD_T.trust_engine_run" in adapter.sql[0]
    assert "'TRUSTED'" in adapter.sql[0]


def test_validate_cli_can_publish_trust_summary(monkeypatch, capsys):
    adapter = _RecordingValidationAdapter()
    run = _run([_result("CALLCENTRE-SEM-001", TestStatus.PASSED)])

    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.adapter_from_environment",
        lambda database_url=None: adapter,
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.generate_metadata_tests",
        lambda prefix: [],
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.run_validation",
        lambda prefix, adapter, tests, **kwargs: run,
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.write_json_report",
        lambda run, output_path: None,
    )

    exit_code = main(["validate", "--prefix", "CallCentre", "--publish-trust-table"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Trust summary published: CallCentre_SEM_STD_T.trust_engine_run" in captured.out
    assert len(adapter.sql) == 1
    assert "INSERT INTO CallCentre_SEM_STD_T.trust_engine_run" in adapter.sql[0]


def test_trust_publish_rejects_unqualified_or_unsafe_table_names():
    run = _run([_result("CALLCENTRE-SEM-001", TestStatus.PASSED)])

    with pytest.raises(ValueError, match="ADPTrust.InvalidTrustTable"):
        trust_result_insert_sql(run, [], "trust_engine_run")


def _patch_validate_pipeline(monkeypatch, adapter, run):
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.adapter_from_environment",
        lambda database_url=None: adapter,
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.generate_metadata_tests",
        lambda prefix: [],
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.run_validation",
        lambda prefix, adapter, tests, **kwargs: run,
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.write_json_report",
        lambda run, output_path: None,
    )


def test_rule_config_publish_target_used_when_flag_has_no_value(monkeypatch, capsys, tmp_path):
    adapter = _RecordingValidationAdapter()
    run = _run([_result("CALLCENTRE-SEM-001", TestStatus.PASSED)])
    _patch_validate_pipeline(monkeypatch, adapter, run)
    rules = tmp_path / "rules.json"
    rules.write_text(
        '{"publish_trust_table": "CallCentre_OBS_STD_T.trust_engine_run"}',
        encoding="utf-8",
    )

    exit_code = main(
        [
            "validate",
            "--prefix",
            "CallCentre",
            "--rules-config",
            str(rules),
            "--publish-trust-table",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Trust summary published: CallCentre_OBS_STD_T.trust_engine_run" in captured.out
    assert "INSERT INTO CallCentre_OBS_STD_T.trust_engine_run" in adapter.sql[0]


def test_explicit_cli_publish_target_overrides_rule_config(monkeypatch, capsys, tmp_path):
    adapter = _RecordingValidationAdapter()
    run = _run([_result("CALLCENTRE-SEM-001", TestStatus.PASSED)])
    _patch_validate_pipeline(monkeypatch, adapter, run)
    rules = tmp_path / "rules.json"
    rules.write_text(
        '{"publish_trust_table": "CallCentre_OBS_STD_T.trust_engine_run"}',
        encoding="utf-8",
    )

    exit_code = main(
        [
            "validate",
            "--prefix",
            "CallCentre",
            "--rules-config",
            str(rules),
            "--publish-trust-table",
            "CallCentre_ALT_STD_T.trust_engine_run",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Trust summary published: CallCentre_ALT_STD_T.trust_engine_run" in captured.out


def test_rule_config_rejects_malformed_publish_target(tmp_path):
    from ai_native_data_product_trust_engine.rule_config import load_rule_config

    rules = tmp_path / "rules.json"
    rules.write_text('{"publish_trust_table": "trust_engine_run"}', encoding="utf-8")

    with pytest.raises(ValueError, match="publish_trust_table must be a two-part"):
        load_rule_config(rules)


def test_rule_config_accepts_partial_files(tmp_path):
    from ai_native_data_product_trust_engine.rule_config import load_rule_config

    rules = tmp_path / "rules.json"
    rules.write_text('{"publish_trust_table": "Db_OBS_STD_T.trust_engine_run"}', encoding="utf-8")

    config = load_rule_config(rules)
    assert config.publish_trust_table == "Db_OBS_STD_T.trust_engine_run"
    assert config.disabled_test_ids == frozenset()
    assert config.disabled_scanners == frozenset()


def _run(results):
    return ValidationRun(
        prefix="CallCentre",
        started_at="2026-06-01T10:00:00+10:00",
        completed_at="2026-06-01T10:00:01+10:00",
        results=results,
    )


def _result(
    test_id,
    status,
    severity=TestSeverity.WARNING,
    sample_rows=None,
):
    return TestResult(
        test_case=TestCase(
            test_id=test_id,
            name="Metadata stays current",
            category=TestCategory.SEMANTIC,
            severity=severity,
            sql="SELECT 1;",
            expected_result="No stale metadata rows.",
            expected=ExpectedResult.ZERO_ROWS,
            repair_strategy="Refresh metadata.",
        ),
        status=status,
        row_count=0 if status == TestStatus.PASSED else 1,
        sample_rows=sample_rows or [],
    )


class _RecordingAdapter:
    def __init__(self):
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)


class _RecordingValidationAdapter(_RecordingAdapter):
    def fetch_all(self, sql):
        return []
