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
        "CALLCENTRE-SEM-004",
        "CALLCENTRE-STRUCT-001",
        "CALLCENTRE-DISCOVERY-001",
        "CALLCENTRE-DISCOVERY-002",
        "CALLCENTRE-QUERY-001",
        "CALLCENTRE-STRUCT-002",
        "CALLCENTRE-STRUCT-003",
        "CALLCENTRE-PERF-001",
    ]
    assert all("CallCentre" in test.sql for test in tests)
    assert tests[7].expected == ExpectedResult.NON_EMPTY
    assert "COUNT(DISTINCT type_signature) > 1" in tests[4].sql


def test_generate_metadata_tests_includes_data_product_registry_table_contract():
    tests = generate_metadata_tests("CallCentre")
    registry_table_test = next(test for test in tests if test.test_id == "CALLCENTRE-DISCOVERY-001")

    assert registry_table_test.category == TestCategory.SEMANTIC
    assert registry_table_test.severity == TestSeverity.CRITICAL
    assert "FROM DBC.TablesV tv" in registry_table_test.sql
    assert "CallCentre_SEM_STD_T.data_product_registry" in registry_table_test.sql
    assert "tv.DatabaseName = 'CallCentre_SEM_STD_T'" in registry_table_test.sql
    assert "MISSING_DATA_PRODUCT_REGISTRY_TABLE" in registry_table_test.sql
    assert "TableKind = 'T'" in registry_table_test.sql


def test_generate_metadata_tests_includes_data_product_registry_contract():
    tests = generate_metadata_tests("CallCentre")
    registry_test = next(test for test in tests if test.test_id == "CALLCENTRE-DISCOVERY-002")

    assert registry_test.category == TestCategory.SEMANTIC
    assert registry_test.severity == TestSeverity.CRITICAL
    assert "FROM CallCentre_SEM_STD_T.data_product_registry" in registry_test.sql
    assert "semantic_database = 'CallCentre_SEM_STD_T'" in registry_test.sql
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


def test_generate_metadata_tests_includes_relationship_datatype_contract():
    tests = generate_metadata_tests("CallCentre")
    datatype_test = next(test for test in tests if test.test_id == "CALLCENTRE-SEM-004")

    assert datatype_test.category == TestCategory.SEMANTIC
    assert datatype_test.severity == TestSeverity.CRITICAL
    assert "FROM CallCentre_SEM_STD_V.table_relationship tr" in datatype_test.sql
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
    assert "FROM DBC.IndicesV iv" in pi_test.sql
    assert "iv.IndexNumber = 1" in pi_test.sql
    assert "iv.IndexType IN ('P', 'Q', 'A', 'K')" in pi_test.sql
    assert "PRIMARY_INDEX_NOT_DEFINED" in pi_test.sql
    assert "PRIMARY_INDEX_NULLABLE_COLUMN" in pi_test.sql
    assert "PRIMARY_INDEX_LOW_CARDINALITY_SUSPECT" in pi_test.sql
    assert "PRIMARY_INDEX_SKEW_HIGH" in pi_test.sql
    assert "primary_index_columns" in pi_test.sql
    assert "intentional designs" in pi_test.repair_strategy


def test_generate_tests_cli_includes_free_text_cases(capsys):
    exit_code = main(["generate-tests", "--prefix", "CallCentre"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CALLCENTRE-CAP-003\tCAPABILITY\tSemantic search claims align to deployed capability" in (
        captured.out
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

    def fail_validation(prefix, adapter, tests):
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
