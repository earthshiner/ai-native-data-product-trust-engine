import json
import logging
from pathlib import Path

from ai_native_data_product_trust_engine.adapters import (
    LoggingAdapter,
    _teradatasql_args_from_url,
)
from ai_native_data_product_trust_engine.cli import _summarise_error, main
from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    TestCase,
    TestCategory,
    TestSeverity,
    TestStatus,
)
from ai_native_data_product_trust_engine.repairs import classify_stale_relationship_path_name
from ai_native_data_product_trust_engine.rule_config import RuleConfig, load_rule_config
from ai_native_data_product_trust_engine.test_generation import generate_metadata_tests
from ai_native_data_product_trust_engine.validators import run_test_case, run_validation


def test_generate_metadata_tests_includes_core_contracts():
    tests = generate_metadata_tests("CallCentre")

    assert [test.test_id for test in tests] == [
        "CALLCENTRE-SEM-001",
        "CALLCENTRE-SEM-002",
        "CALLCENTRE-SEM-003",
        "CALLCENTRE-SEM-004",
        "CALLCENTRE-STRUCT-001",
        "CALLCENTRE-SEM-005",
        "CALLCENTRE-SEM-006",
        "CALLCENTRE-SEM-007",
        "CALLCENTRE-SEM-008",
        "CALLCENTRE-SEM-010",
        "CALLCENTRE-SEM-011",
        "CALLCENTRE-DISCOVERY-001",
        "CALLCENTRE-DISCOVERY-002",
        "CALLCENTRE-QUERY-001",
        "CALLCENTRE-STRUCT-002",
        "CALLCENTRE-STRUCT-003",
        "CALLCENTRE-PERF-001",
        "CALLCENTRE-OPS-001",
        "CALLCENTRE-OPS-002",
        "CALLCENTRE-OPS-003",
    ]
    assert all(
        "CallCentre" in test.sql
        for test in tests
        if test.test_id != "CALLCENTRE-DISCOVERY-001"
    )
    # Index shifted by one after SEM-009 was retired (now at 13, was 14).
    assert tests[13].expected == ExpectedResult.NON_EMPTY
    assert "COUNT(DISTINCT type_signature) > 1" in tests[4].sql
    assert tests[4].name == "Similar table column names use consistent datatypes"
    assert "tv.TableKind = 'T'" in tests[4].sql
    assert "tv.TableKind IN ('T', 'V')" not in tests[4].sql


def test_generated_metadata_tests_scope_to_deployed_modules():
    tests = generate_metadata_tests("CallCentre")
    scoped_test_ids = {
        "CALLCENTRE-SEM-001",
        "CALLCENTRE-SEM-002",
        "CALLCENTRE-SEM-003",
        "CALLCENTRE-SEM-004",
        "CALLCENTRE-STRUCT-001",
        "CALLCENTRE-SEM-005",
        "CALLCENTRE-SEM-006",
        "CALLCENTRE-SEM-007",
        "CALLCENTRE-SEM-008",
        "CALLCENTRE-SEM-010",
        "CALLCENTRE-STRUCT-002",
        "CALLCENTRE-STRUCT-003",
        "CALLCENTRE-PERF-001",
    }

    scoped_tests = [test for test in tests if test.test_id in scoped_test_ids]

    assert scoped_tests
    assert all("deployment_status" in test.sql for test in scoped_tests)
    assert all(
        "data_product_map module_scope" in test.sql
        for test in scoped_tests
        if test.test_id != "CALLCENTRE-SEM-007"
    )


def test_generate_metadata_tests_includes_central_registry_view_contract():
    tests = generate_metadata_tests("CallCentre")
    registry_table_test = next(test for test in tests if test.test_id == "CALLCENTRE-DISCOVERY-001")

    assert registry_table_test.category == TestCategory.SEMANTIC
    assert registry_table_test.severity == TestSeverity.CRITICAL
    assert "FROM DBC.TablesV tv" in registry_table_test.sql
    assert "DataProductsMaster_GOV_BUS_V" in registry_table_test.sql
    assert "active_data_product_registry" in registry_table_test.sql
    assert "MISSING_ACTIVE_DATA_PRODUCT_REGISTRY_VIEW" in registry_table_test.sql
    assert "TableKind IN ('V', 'O', 'Q')" in registry_table_test.sql


def test_generate_metadata_tests_includes_central_registry_contract():
    tests = generate_metadata_tests("CallCentre")
    registry_test = next(test for test in tests if test.test_id == "CALLCENTRE-DISCOVERY-002")

    assert registry_test.category == TestCategory.SEMANTIC
    assert registry_test.severity == TestSeverity.CRITICAL
    assert "FROM DataProductsMaster_GOV_BUS_V.active_data_product_registry" in registry_test.sql
    assert "UPPER(TRIM(product_status)) = 'ACTIVE'" in registry_test.sql
    assert "semantic_view_database = 'CallCentre_SEM_STD_V'" in registry_test.sql
    assert "       ,semantic_view_database" in registry_test.sql
    assert "       ,memory_view_database" in registry_test.sql
    assert "       ,observability_view_database" in registry_test.sql
    assert "FROM DBC.DBCInfoV\n    WHERE InfoKey = 'VERSION'\n      AND NOT EXISTS" in (
        registry_test.sql
    )
    assert "manifest_json" in registry_test.sql
    assert "MISSING_PRODUCT_REGISTRY_ROW" in registry_test.sql
    assert "SEMANTIC_DATABASE_NOT_IN_MODULE_MAP" in registry_test.sql
    assert "MEMORY_DATABASE_NOT_IN_MODULE_MAP" in registry_test.sql
    assert "approved_entrypoint" in registry_test.sql
    assert "manifest_json" in registry_test.repair_strategy


def test_generate_metadata_tests_includes_statistics_coverage_contract():
    tests = generate_metadata_tests("CallCentre")
    stats_test = next(test for test in tests if test.test_id == "CALLCENTRE-PERF-001")

    assert stats_test.category == TestCategory.PERFORMANCE
    assert stats_test.severity == TestSeverity.WARNING
    assert "FROM DBC.ColumnStatsV statv" in stats_test.sql
    assert "DBC.ColumnStatsV cs" not in stats_test.sql
    assert "MISSING_JOIN_COLUMN_STATS" in stats_test.sql
    assert "COLLECT STATISTICS COLUMN" in stats_test.sql


def test_generate_metadata_tests_include_semantic_access_layer_contracts():
    tests = generate_metadata_tests("CallCentre")
    datatype_test = next(test for test in tests if test.test_id == "CALLCENTRE-SEM-005")
    coverage_test = next(test for test in tests if test.test_id == "CALLCENTRE-SEM-006")
    primary_views_test = next(test for test in tests if test.test_id == "CALLCENTRE-SEM-007")
    entity_view_test = next(test for test in tests if test.test_id == "CALLCENTRE-SEM-008")
    relationship_access_test = next(test for test in tests if test.test_id == "CALLCENTRE-SEM-010")
    lineage_access_test = next(test for test in tests if test.test_id == "CALLCENTRE-SEM-011")

    assert datatype_test.severity == TestSeverity.WARNING
    assert "COLUMN_METADATA_DATATYPE_MISMATCH" in datatype_test.sql
    assert "DBC.ColumnsV colv" in datatype_test.sql
    assert "Refresh column_metadata.data_type" in datatype_test.repair_strategy

    assert "MISSING_COLUMN_METADATA" in coverage_test.sql
    assert "CallCentre_SEM_STD_V.column_metadata" in coverage_test.sql

    assert primary_views_test.severity == TestSeverity.CRITICAL
    assert "FROM DBC.ColumnsV colv" in primary_views_test.precondition_sql
    assert "CallCentre_SEM_STD_V" in primary_views_test.precondition_sql
    assert "DATA_PRODUCT_MAP_PRIMARY_VIEWS_MISSING" in primary_views_test.precondition_sql
    assert "STRTOK_SPLIT_TO_TABLE" not in primary_views_test.sql
    assert "REGEXP_SUBSTR(dpm.primary_views" in primary_views_test.sql
    assert "FROM DBC.DBCInfoV WHERE InfoKey = 'VERSION'" in primary_views_test.sql
    assert "PRIMARY_VIEW_NOT_DEPLOYED" in primary_views_test.sql
    assert "_BUS_V" in primary_views_test.sql

    assert entity_view_test.severity == TestSeverity.CRITICAL
    assert "ENTITY_VIEW_NAME_MISSING" in entity_view_test.sql
    assert "ENTITY_VIEW_NAME_NOT_DEPLOYED" in entity_view_test.sql
    # entity_metadata.view_name is stored fully-qualified (db.object), so the
    # deployment join must compare against the qualified DBC name — comparing the
    # unqualified tv.TableName falsely flags every deployed view as NOT_DEPLOYED.
    assert (
        "TRIM(tv.DatabaseName) || '.' || TRIM(tv.TableName) = TRIM(re.view_name)"
        in entity_view_test.sql
    )
    assert "tv.TableName = ae.view_name" not in entity_view_test.sql
    # Evidence enrichment consumed by the repairs generator: the resolved BUS_V
    # view, whether it is deployed, and the STD_T base table to UPDATE.
    assert "AS derived_view_name" in entity_view_test.sql
    assert "AS derived_view_deployed" in entity_view_test.sql
    assert "'CallCentre_SEM_STD_T' AS metadata_database_name" in entity_view_test.sql
    assert "'entity_metadata' AS metadata_table_name" in entity_view_test.sql

    # SEM-009 (Entity deleted flag metadata) was retired — see test_generation.py.
    assert not any(t.test_id == "CALLCENTRE-SEM-009" for t in tests)

    assert relationship_access_test.severity == TestSeverity.CRITICAL
    assert "RELATIONSHIP_SOURCE_NOT_BUS_V" in relationship_access_test.sql
    assert "RELATIONSHIP_TARGET_NOT_BUS_V" in relationship_access_test.sql
    assert "ORDER BY 1, 5, 2, 3, 4" in relationship_access_test.sql
    assert "table_relationship source_database/source_table/source_column" in (
        relationship_access_test.inspection_scope
    )

    assert lineage_access_test.severity == TestSeverity.WARNING
    assert "FROM DBC.TablesV tv" in lineage_access_test.precondition_sql
    assert "CallCentre_OBS_STD_V" in lineage_access_test.precondition_sql
    assert "LINEAGE_VIEW_NOT_DEPLOYED" in lineage_access_test.precondition_sql
    assert "FROM CallCentre_OBS_STD_V.data_lineage" in lineage_access_test.sql
    assert "LINEAGE_SOURCE_NOT_BUS_V" in lineage_access_test.sql
    assert "LINEAGE_TARGET_NOT_BUS_V" in lineage_access_test.sql
    assert "ORDER BY 1, 4, 2, 3" in lineage_access_test.sql


def test_generate_metadata_tests_exclude_backup_objects():
    tests = generate_metadata_tests("CallCentre")
    inventory_test_ids = {
        "CALLCENTRE-SEM-001",
        "CALLCENTRE-SEM-002",
        "CALLCENTRE-SEM-003",
        "CALLCENTRE-SEM-004",
        "CALLCENTRE-STRUCT-001",
        "CALLCENTRE-SEM-005",
        "CALLCENTRE-SEM-006",
        "CALLCENTRE-SEM-008",
        "CALLCENTRE-SEM-010",
        "CALLCENTRE-STRUCT-002",
        "CALLCENTRE-STRUCT-003",
        "CALLCENTRE-PERF-001",
    }
    inventory_tests = [test for test in tests if test.test_id in inventory_test_ids]

    assert inventory_tests
    assert all("_BKP" in test.sql for test in inventory_tests)
    assert all("_BK" in test.sql for test in inventory_tests)


def test_generate_metadata_tests_includes_relationship_datatype_contract():
    tests = generate_metadata_tests("CallCentre")
    datatype_test = next(test for test in tests if test.test_id == "CALLCENTRE-SEM-004")

    assert datatype_test.category == TestCategory.SEMANTIC
    assert datatype_test.severity == TestSeverity.CRITICAL
    assert "FROM CallCentre_SEM_STD_V.table_relationship tr" in datatype_test.sql
    assert "ORDER BY 1, 18, 2, 3, 4" in datatype_test.sql
    assert "INNER JOIN DBC.ColumnsV src" in datatype_test.sql
    assert "INNER JOIN DBC.ColumnsV tgt" in datatype_test.sql
    assert "JOIN_COLUMN_TYPE_MISMATCH" in datatype_test.sql
    assert "JOIN_COLUMN_CHARSET_MISMATCH" in datatype_test.sql
    assert "JOIN_COLUMN_PRECISION_SCALE_MISMATCH" in datatype_test.sql
    assert "JOIN_COLUMN_LENGTH_MISMATCH" in datatype_test.sql
    assert "source_decimal_total_digits" in datatype_test.sql
    assert "target_decimal_fractional_digits" in datatype_test.sql
    assert "character set" in datatype_test.repair_strategy


def test_generate_metadata_tests_includes_table_skew_contract():
    tests = generate_metadata_tests("CallCentre")
    table_skew_test = next(test for test in tests if test.test_id == "CALLCENTRE-STRUCT-002")

    assert table_skew_test.test_id == "CALLCENTRE-STRUCT-002"
    assert table_skew_test.severity == TestSeverity.WARNING
    assert "FROM DBC.TableSizeV tsv" in table_skew_test.sql
    assert "HASHROW(Tablename)" not in table_skew_test.sql
    assert "skew_percent > 20" in table_skew_test.sql


def test_generate_metadata_tests_includes_primary_index_health_contract():
    tests = generate_metadata_tests("CallCentre")
    pi_test = next(test for test in tests if test.test_id == "CALLCENTRE-STRUCT-003")

    assert pi_test.category == TestCategory.STRUCTURAL
    assert pi_test.severity == TestSeverity.WARNING
    assert "primary_index_column_rows AS" in pi_test.sql
    assert "FROM DBC.IndicesV iv" in pi_test.sql
    assert "iv.IndexNumber = 1" in pi_test.sql
    assert "iv.IndexType IN ('P', 'Q', 'A', 'K')" in pi_test.sql
    assert "MIN(picr.column_name) AS primary_index_columns" in pi_test.sql
    assert "LISTAGG(" not in pi_test.sql
    assert "PRIMARY_INDEX_NOT_DEFINED" in pi_test.sql
    assert "PRIMARY_INDEX_NULLABLE_COLUMN" in pi_test.sql
    assert "PRIMARY_INDEX_LOW_CARDINALITY_SUSPECT" in pi_test.sql
    assert "PRIMARY_INDEX_SKEW_HIGH" in pi_test.sql
    assert "ORDER BY 6, 5 DESC, 4 DESC, 1, 2" in pi_test.sql
    assert "primary_index_columns" in pi_test.sql
    assert "intentional designs" in pi_test.repair_strategy


def test_generate_metadata_tests_includes_operational_readiness_contracts():
    tests = generate_metadata_tests("CallCentre")
    module_test = next(test for test in tests if test.test_id == "CALLCENTRE-OPS-001")
    objects_test = next(test for test in tests if test.test_id == "CALLCENTRE-OPS-002")
    bus_views_test = next(test for test in tests if test.test_id == "CALLCENTRE-OPS-003")

    assert module_test.category == TestCategory.OPERATIONAL
    assert module_test.severity == TestSeverity.WARNING
    assert "MISSING_OBSERVABILITY_MODULE" in module_test.sql
    assert "OBSERVABILITY_DATABASE_NOT_DEPLOYED" in module_test.sql
    assert "FROM DBC.DBCInfoV\n    WHERE InfoKey = 'VERSION'\n      AND NOT EXISTS" in (
        module_test.sql
    )
    assert "ORDER BY 2, 1" in module_test.sql
    assert "FROM CallCentre_SEM_STD_V.data_product_map" in module_test.sql

    assert objects_test.category == TestCategory.OPERATIONAL
    assert objects_test.severity == TestSeverity.WARNING
    assert "change_event" in objects_test.sql
    assert "data_quality_metric" in objects_test.sql
    assert "data_lineage" in objects_test.sql
    assert "lineage_run" in objects_test.sql
    assert "lineage_graph" in objects_test.sql
    assert "lineage_run_latest" in objects_test.sql
    assert "MISSING_OBSERVABILITY_TABLE" in objects_test.sql
    assert "MISSING_OBSERVABILITY_SEMANTIC_VIEW" in objects_test.sql
    assert "CAST('change_event' AS VARCHAR(128)) AS object_name" in objects_test.sql
    assert "UNION ALL SELECT CAST('lineage_run_latest' AS VARCHAR(128))" in objects_test.sql

    assert bus_views_test.category == TestCategory.OPERATIONAL
    assert bus_views_test.severity == TestSeverity.CRITICAL
    assert "CallCentre_OBS_BUS_V" in bus_views_test.sql
    assert "MISSING_OBSERVABILITY_BUS_VIEW" in bus_views_test.sql
    assert "CAST('change_event' AS VARCHAR(128)) AS object_name" in bus_views_test.sql
    assert "UNION ALL SELECT CAST('agent_outcome' AS VARCHAR(128))" in bus_views_test.sql
    assert "ORDER BY 3, 1, 2" in objects_test.sql
    assert "change_event/data_quality_metric/data_lineage/lineage_run" in (
        objects_test.inspection_scope
    )


def test_rule_config_filters_disabled_test_ids_and_scanners(monkeypatch):
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, encoding=None: json.dumps(
            {
                "disabled_test_ids": ["callcentre-sem-008"],
                "disabled_scanners": ["view", "text"],
            }
        ),
    )

    config = load_rule_config(Path("config/rules.json"))
    filtered_tests = config.filter_tests(generate_metadata_tests("CallCentre"))

    assert "CALLCENTRE-SEM-008" not in {test.test_id for test in filtered_tests}
    assert config.scanner_kwargs()["include_view_contract_scans"] is False
    assert config.scanner_kwargs()["include_text_reference_scans"] is False
    assert config.scanner_kwargs()["include_capability_scans"] is True
    excluded_checks = config.excluded_checks(generate_metadata_tests("CallCentre"))
    excluded_ids = {check.check_id for check in excluded_checks}
    assert "CALLCENTRE-SEM-008" in excluded_ids
    assert "SCANNER:TEXT" in excluded_ids
    assert "SCANNER:VIEW" in excluded_ids
    assert any("glossary text" in check.reason for check in excluded_checks)


def test_generate_tests_cli_applies_rule_config(monkeypatch, capsys):
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.load_rule_config",
        lambda path: RuleConfig(
            disabled_test_ids=frozenset({"CALLCENTRE-SEM-008"}),
            disabled_scanners=frozenset({"VIEW", "TEXT"}),
        ),
    )

    exit_code = main(
        ["generate-tests", "--prefix", "CallCentre", "--rules-config", "config/rules.json"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CALLCENTRE-SEM-008" not in captured.out
    assert "CALLCENTRE-TEXT-001" not in captured.out
    assert "CALLCENTRE-VIEW-COLUMNS" not in captured.out
    assert "CALLCENTRE-CAP-003" in captured.out


def test_generate_tests_cli_includes_free_text_cases(capsys):
    exit_code = main(["generate-tests", "--prefix", "CallCentre"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert (
        "CALLCENTRE-OPS-001\tOPERATIONAL\tObservability module is registered and deployed"
        in captured.out
    )
    assert "CALLCENTRE-CAP-003\tCAPABILITY\tSemantic search claims align to deployed capability" in (
        captured.out
    )
    assert (
        "CALLCENTRE-REL-ORPHANS\tDATA_QUALITY\tDeclared relationships have bounded orphan evidence"
        in captured.out
    )
    assert (
        "CALLCENTRE-TEMPORAL-CURRENT\tDATA_QUALITY\tTemporal entities have valid current-record contracts"
        in captured.out
    )
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


def test_run_test_case_returns_precondition_findings_without_running_dependent_sql():
    finding = {
        "database_name": "CallCentre_OBS_STD_V",
        "object_name": "data_lineage",
        "issue_code": "LINEAGE_VIEW_NOT_DEPLOYED",
    }
    adapter = SequencedStubAdapter([[finding]])
    test_case = TestCase(
        test_id="CALLCENTRE-SEM-011",
        name="Lineage metadata exposes BUS_V access endpoints",
        category=TestCategory.SEMANTIC,
        severity=TestSeverity.WARNING,
        sql="SELECT 1 FROM CallCentre_OBS_STD_V.data_lineage;",
        expected_result="Returns zero rows.",
        precondition_sql="SELECT 1 FROM DBC.TablesV;",
    )

    result = run_test_case(adapter, test_case)

    assert result.status == TestStatus.FAILED
    assert result.row_count == 1
    assert result.sample_rows == [finding]
    assert adapter.sql_calls == ["SELECT 1 FROM DBC.TablesV;"]


def test_run_test_case_runs_dependent_sql_when_precondition_passes():
    adapter = SequencedStubAdapter([[], []])
    test_case = TestCase(
        test_id="CALLCENTRE-SEM-011",
        name="Lineage metadata exposes BUS_V access endpoints",
        category=TestCategory.SEMANTIC,
        severity=TestSeverity.WARNING,
        sql="SELECT 1 FROM CallCentre_OBS_STD_V.data_lineage;",
        expected_result="Returns zero rows.",
        precondition_sql="SELECT 1 FROM DBC.TablesV;",
    )

    result = run_test_case(adapter, test_case)

    assert result.status == TestStatus.PASSED
    assert adapter.sql_calls == [
        "SELECT 1 FROM DBC.TablesV;",
        "SELECT 1 FROM CallCentre_OBS_STD_V.data_lineage;",
    ]


def test_run_test_case_backend_error_includes_inspection_context():
    test_case = TestCase(
        test_id="CALLCENTRE-SEM-010",
        name="Relationship metadata uses BUS_V access endpoints",
        category=TestCategory.SEMANTIC,
        severity=TestSeverity.CRITICAL,
        sql="SELECT 1 FROM CallCentre_SEM_STD_V.table_relationship;",
        expected_result="Returns zero rows.",
        repair_strategy="Move relationship metadata to BUS_V databases.",
        inspection_scope="CallCentre_SEM_STD_V.table_relationship source and target endpoints",
    )
    result = run_test_case(FailingScannerAdapter(), test_case)

    assert result.status.value == "ERROR"
    assert result.sample_rows[0]["issue_code"] == "BACKEND_ERROR"
    assert (
        result.sample_rows[0]["inspection_scope"]
        == "CallCentre_SEM_STD_V.table_relationship source and target endpoints"
    )
    assert result.sample_rows[0]["inspected_objects"] == [
        "CallCentre_SEM_STD_V.table_relationship"
    ]
    assert result.sample_rows[0]["repair_hint"] == "Move relationship metadata to BUS_V databases."


def test_logging_adapter_records_sql_success_and_failure(caplog):
    caplog.set_level(logging.INFO, logger="ai_native_data_product_trust_engine.sql")
    adapter = LoggingAdapter(StubAdapter([{"answer": 1}]))

    rows = adapter.fetch_all("SELECT 1 AS answer;")

    assert rows == [{"answer": 1}]
    assert "Executing SQL query:\nSELECT 1 AS answer;" in caplog.text
    assert "SQL query returned 1 rows." in caplog.text

    caplog.clear()
    run_test_case(
        LoggingAdapter(FailingScannerAdapter()),
        TestCase(
            test_id="CALLCENTRE-SEM-001",
            name="Generated check",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql="SELECT 1 FROM CallCentre_SEM_STD_V.data_product_map;",
            expected_result="Returns zero rows.",
            expected=ExpectedResult.ZERO_ROWS,
        ),
    )

    assert (
        "SQL query failed:\nSELECT 1 FROM CallCentre_SEM_STD_V.data_product_map;"
        not in caplog.text
    )
    assert "SQL query failed:" in caplog.text
    assert "gosqldriver/stack" not in caplog.text


def test_logging_adapter_normalises_carriage_returns_in_failed_sql(caplog):
    caplog.set_level(logging.ERROR, logger="ai_native_data_product_trust_engine.sql")

    run_test_case(
        LoggingAdapter(FailingScannerAdapter()),
        TestCase(
            test_id="CALLCENTRE-QUERY-001",
            name="Generated check",
            category=TestCategory.QUERY,
            severity=TestSeverity.CRITICAL,
            sql="SELECT 1\rFROM db.table\rORDER BY 1;",
            expected_result="Returns zero rows.",
        ),
    )

    assert "\r" not in caplog.text
    assert "SELECT 1\nFROM db.table\nORDER BY 1;" in caplog.text


def test_validate_cli_writes_report(monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    html_path = tmp_path / "report.html"

    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.adapter_from_environment",
        lambda database_url=None: ValidationStubAdapter(),
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.generate_metadata_tests",
        lambda prefix: [_test_case(ExpectedResult.ZERO_ROWS)],
    )

    exit_code = main(
        [
            "validate",
            "--prefix",
            "CallCentre",
            "--output",
            str(report_path),
            "--html-output",
            str(html_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["passed"] == 11
    assert "CallCentre trust report" in html_path.read_text(encoding="utf-8")


def test_validate_cli_traps_uncaught_backend_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.adapter_from_environment",
        lambda database_url=None: ValidationStubAdapter(),
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.generate_metadata_tests",
        lambda prefix: [_test_case(ExpectedResult.ZERO_ROWS)],
    )

    def fail_validation(prefix, adapter, tests, **kwargs):
        raise RuntimeError(
            "[Version 20.0.0.56] [Session 0] [Teradata SQL Driver] "
            "Failed to connect to example.teradata.com\n at gosqldriver/stack"
        )

    monkeypatch.setattr("ai_native_data_product_trust_engine.cli.run_validation", fail_validation)

    exit_code = main(["validate", "--prefix", "CallCentre"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "[ADPTrust.ValidationFailed]" in captured.err
    assert "Failed to connect to example.teradata.com" in captured.err
    assert "gosqldriver/stack" not in captured.err
    assert "Traceback" not in captured.err


def test_run_validation_records_scanner_errors_as_results():
    run = run_validation(
        "CallCentre",
        FailingScannerAdapter(),
        [],
        include_capability_scans=False,
        include_query_template_scans=False,
        include_relationship_health_scans=False,
        include_text_reference_scans=False,
        include_view_contract_scans=True,
    )

    assert run.error_count == 1
    result = run.results[0]
    assert result.test_case.test_id == "CALLCENTRE-VIEW-SCAN"
    assert result.status.value == "ERROR"
    assert result.sample_rows[0]["issue_code"] == "SCANNER_BACKEND_ERROR"
    assert "CallCentre_DOM_BUS_V.Call_H" in result.error_message


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


class SequencedStubAdapter:
    def __init__(self, row_sets):
        self.row_sets = iter(row_sets)
        self.sql_calls = []

    def fetch_all(self, sql):
        self.sql_calls.append(sql)
        return next(self.row_sets)


class ValidationStubAdapter:
    def fetch_all(self, sql):
        if sql.startswith("EXPLAIN"):
            return [{"Explain": "ok"}]
        if sql.startswith("HELP COLUMN"):
            return [{"Column Name": "call_id"}]
        if "COALESCE(RequestText" in sql:
            return []
        if "primary_index_issues AS" in sql:
            return []
        if "standard_tables AS" in sql:
            return []
        if "FROM DBC.TablesV" in sql and "TableKind = 'V'" in sql:
            return [
                {
                    "database_name": "CallCentre_DOM_BUS_V",
                    "view_name": "Call_Enriched",
                }
            ]
        return []


class FailingScannerAdapter:
    def fetch_all(self, sql):
        raise RuntimeError(
            "[Version 20.0.0.56] [Teradata Database] [Error 3807] "
            "Object 'CallCentre_DOM_BUS_V.Call_H' does not exist.\n at gosqldriver/stack"
        )

    def execute(self, sql):
        raise AssertionError("execute should not be called")


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
