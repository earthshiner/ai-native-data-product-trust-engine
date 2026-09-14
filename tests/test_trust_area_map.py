"""The per-area trust map (trust heatmap) the Data Product Browser renders."""

import pytest

from ai_native_data_product_trust_engine.cli import main
from ai_native_data_product_trust_engine.models import (
    ExcludedCheck,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.trust_area_map import (
    area_for_check,
    build_trust_areas,
    default_trust_area_table,
    publish_trust_area_map,
    read_deployed_modules,
    trust_area_map_ddl,
    trust_area_map_sql,
)


def _result(
    test_id, status=TestStatus.PASSED, severity=TestSeverity.WARNING, name=None, repair=None
):
    case = TestCase(
        test_id=test_id,
        name=name or f"Check {test_id}",
        category=TestCategory.SEMANTIC,
        severity=severity,
        sql="SELECT 1;",
        expected_result="Returns zero rows.",
        repair_strategy=repair,
    )
    return TestResult(
        test_case=case, status=status, row_count=0 if status == TestStatus.PASSED else 2
    )


def _run(results, excluded=None):
    return ValidationRun(
        prefix="CallCentre",
        started_at="2026-09-15T01:00:00+00:00",
        completed_at="2026-09-15T01:05:00+00:00",
        results=results,
        excluded_checks=excluded or [],
    )


def _by_name(areas):
    return {(a.scope_type, a.scope_name): a for a in areas}


@pytest.mark.parametrize(
    "check_id, area",
    [
        ("CALLCENTRE-SEM-001", ("module", "Semantic")),
        ("CALLCENTRE-SEM-011", ("module", "Observability")),
        ("CALLCENTRE-DISCOVERY-002", ("module", "Semantic")),
        ("CALLCENTRE-TEXT-004", ("module", "Memory")),
        ("CALLCENTRE-QUERY-EXPLAIN-QC-SEMANTIC-001", ("module", "Memory")),
        ("CALLCENTRE-REL-ORPHANS-customer_ticket", ("module", "Domain")),
        ("CALLCENTRE-TEMPORAL-CURRENT-ticket", ("module", "Domain")),
        ("CALLCENTRE-STRUCT-002", ("pattern", "physical-design")),
        ("CALLCENTRE-PERF-001", ("pattern", "physical-design")),
        ("CALLCENTRE-CAP-002", ("capability", "capability-claims")),
        ("SCANNER:VIEW", ("pattern", "view-contracts")),
        ("CALLCENTRE-CAPABILITY-SCAN", ("capability", "capability-claims")),
        ("CALLCENTRE-RELATIONSHIP-SCAN", ("module", "Domain")),
        ("CALLCENTRE-VIEW-TABLE-LOCKING", ("pattern", "view-contracts")),
        ("CALLCENTRE-STD-VIEW-1TO1-CallCentre_DOM_STD_V.Ticket", ("pattern", "view-contracts")),
        ("CALLCENTRE-BUS-VIEW-SOURCES", ("pattern", "view-contracts")),
        ("CALLCENTRE-NEWTHING-001", ("pattern", "unassigned-checks")),
    ],
)
def test_checks_map_to_areas_by_family(check_id, area):
    assert area_for_check(check_id, "CallCentre") == area


def test_full_pass_is_strong_with_no_gaps():
    areas = _by_name(
        build_trust_areas(_run([_result("CALLCENTRE-SEM-001"), _result("CALLCENTRE-SEM-002")]))
    )
    semantic = areas[("module", "Semantic")]
    assert (semantic.status, semantic.confidence, semantic.coverage) == ("pass", "strong", 1.0)
    assert semantic.gaps is None and semantic.recommendation is None


def test_blocking_failure_is_weak_and_names_the_check_and_repair():
    results = [
        _result("CALLCENTRE-OPS-001"),
        _result(
            "CALLCENTRE-OPS-002",
            TestStatus.FAILED,
            TestSeverity.ERROR,
            name="Observability evidence objects are deployed",
            repair="Deploy the Observability evidence tables.",
        ),
    ]
    obs = _by_name(build_trust_areas(_run(results)))[("module", "Observability")]
    assert (obs.status, obs.confidence) == ("fail", "weak")
    assert obs.gaps == "1 failed of 2 checks: Observability evidence objects are deployed."
    assert obs.recommendation == "Deploy the Observability evidence tables."


def test_warning_failure_is_partial_confidence():
    results = [
        _result("CALLCENTRE-SEM-001"),
        _result("CALLCENTRE-SEM-002", TestStatus.FAILED, TestSeverity.WARNING),
    ]
    semantic = _by_name(build_trust_areas(_run(results)))[("module", "Semantic")]
    assert (semantic.status, semantic.confidence) == ("fail", "partial")


def test_execution_error_is_weak():
    results = [
        _result("CALLCENTRE-SEM-001"),
        _result("CALLCENTRE-SEM-002", TestStatus.ERROR, TestSeverity.WARNING),
    ]
    semantic = _by_name(build_trust_areas(_run(results)))[("module", "Semantic")]
    assert semantic.confidence == "weak" and "could not run" in semantic.gaps


def test_disabled_checks_lower_coverage_and_are_named():
    excluded = [
        ExcludedCheck(
            "CALLCENTRE-SEM-008",
            "Entity metadata publishes BUS_V view names",
            "SEMANTIC",
            "Disabled.",
        ),
    ]
    run = _run(
        [
            _result("CALLCENTRE-SEM-001"),
            _result("CALLCENTRE-SEM-002"),
            _result("CALLCENTRE-SEM-003"),
        ],
        excluded,
    )
    semantic = _by_name(build_trust_areas(run))[("module", "Semantic")]
    assert (semantic.checks_expected, semantic.checks_ran, semantic.coverage) == (4, 3, 0.75)
    assert (semantic.status, semantic.confidence) == ("partial", "partial")
    assert "1 of 4 checks disabled" in semantic.gaps and "BUS_V view names" in semantic.gaps
    assert semantic.recommendation.startswith("Re-enable")


def test_fully_disabled_area_is_not_validated_and_unknown():
    run = _run(
        [], [ExcludedCheck("SCANNER:VIEW", "View contract scans", "STRUCTURAL", "Disabled.")]
    )
    view = _by_name(build_trust_areas(run))[("pattern", "view-contracts")]
    assert (view.status, view.confidence, view.coverage) == ("not-validated", "unknown", 0.0)


def test_deployed_module_without_checks_is_no_evidence():
    areas = _by_name(
        build_trust_areas(_run([_result("CALLCENTRE-SEM-001")]), ["Search", "Semantic"])
    )
    search = areas[("module", "Search")]
    assert (search.status, search.confidence, search.coverage) == ("no-evidence", "unknown", None)
    assert search.gaps == "No Trust Engine check covers the Search module."
    assert "Add Trust Engine checks for the Search module" in search.recommendation


def test_sql_replaces_the_map_in_one_request_and_escapes_text():
    results = [
        _result(
            "CALLCENTRE-SEM-001", TestStatus.FAILED, name="Customer's metadata", repair="Fix it"
        )
    ]
    run = _run(results)
    sql = trust_area_map_sql(
        run, build_trust_areas(run, ["Search"]), "CallCentre_OBS_STD_T.trust_area_map"
    )
    statements = sql.split(";\n")
    assert statements[0] == "DELETE FROM CallCentre_OBS_STD_T.trust_area_map"
    assert len(statements) == 3 and sql.endswith(";")
    assert "Customer''s metadata." in sql
    assert "TIMESTAMP '2026-09-15 01:05:00+00:00'" in sql
    assert "'module', 'Search', NULL, 'no-evidence', 'unknown'" in sql
    assert "'module', 'Semantic', 1.0000, 'fail'" in sql


def test_table_name_is_validated_and_ddl_matches_the_example_shape():
    with pytest.raises(ValueError):
        trust_area_map_sql(_run([]), [], "trust_area_map; DROP TABLE x")
    assert default_trust_area_table("CallCentre") == "CallCentre_OBS_STD_T.trust_area_map"
    ddl = trust_area_map_ddl("CallCentre")
    for column in (
        "scope_type VARCHAR(20)",
        "coverage DECIMAL(5,4)",
        "measured_dts TIMESTAMP(6) WITH TIME ZONE",
    ):
        assert column in ddl


class _MapAdapter:
    def __init__(self, modules=None, fail=False):
        self.sql = []
        self.modules = modules or []
        self.fail = fail

    def fetch_all(self, sql):
        if self.fail:
            raise RuntimeError("[Error 3807] data_product_map does not exist")
        return [{"MODULE_NAME": m} for m in self.modules]

    def execute(self, sql):
        self.sql.append(sql)


def test_deployed_modules_are_read_and_canonicalised():
    assert read_deployed_modules(_MapAdapter(["observability", "Search"]), "CallCentre") == [
        "Observability",
        "Search",
    ]
    assert read_deployed_modules(_MapAdapter(fail=True), "CallCentre") == []


def test_publish_trust_area_map_writes_one_request():
    adapter = _MapAdapter(["Domain", "Semantic"])
    table, areas = publish_trust_area_map(adapter, _run([_result("CALLCENTRE-SEM-001")]))
    assert table == "CallCentre_OBS_STD_T.trust_area_map"
    assert [(a.scope_name, a.status) for a in areas] == [
        ("Domain", "no-evidence"),
        ("Semantic", "pass"),
    ]
    assert len(adapter.sql) == 1 and adapter.sql[0].startswith(
        "DELETE FROM CallCentre_OBS_STD_T.trust_area_map"
    )


def test_validate_cli_can_publish_trust_area_map(monkeypatch, capsys):
    adapter = _MapAdapter(["Semantic"])
    run = _run([_result("CALLCENTRE-SEM-001")])
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.adapter_from_environment",
        lambda database_url=None: adapter,
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.generate_metadata_tests", lambda prefix: []
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.run_validation",
        lambda prefix, adapter, tests, **kwargs: run,
    )
    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.cli.write_json_report", lambda run, output_path: None
    )

    exit_code = main(["validate", "--prefix", "CallCentre", "--publish-trust-area-map"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Trust area map published: CallCentre_OBS_STD_T.trust_area_map (1 areas)" in captured.out
    assert (
        len(adapter.sql) == 1
        and "INSERT INTO CallCentre_OBS_STD_T.trust_area_map" in adapter.sql[0]
    )
