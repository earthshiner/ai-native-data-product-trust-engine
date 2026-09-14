"""Publishing a run as the standard validation_run and validation_area (wire schema 2.1)."""

import json
import re

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
from ai_native_data_product_trust_engine.validation_publish import (
    PAYLOAD_SCHEMA_VERSION,
    PRODUCER_ID,
    area_for_check,
    build_validation_areas,
    default_validation_database,
    publish_validation,
    read_registered_modules,
    validation_publish_sql,
    validation_run_row,
)

REGISTERED = ["Domain", "Observability", "Prediction", "Search", "Semantic"]


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


def _areas(run, registered=REGISTERED):
    return {(a.scope_kind, a.scope_id): a for a in build_validation_areas(run, registered)}


# -- area assignment ---------------------------------------------------------


@pytest.mark.parametrize(
    "check_id, area",
    [
        ("CALLCENTRE-SEM-001", ("MODULE", "Semantic")),
        ("CALLCENTRE-SEM-011", ("MODULE", "Observability")),
        ("CALLCENTRE-DISCOVERY-002", ("MODULE", "Semantic")),
        ("CALLCENTRE-OPS-003", ("MODULE", "Observability")),
        ("CALLCENTRE-REL-ORPHANS-customer_ticket", ("MODULE", "Domain")),
        ("CALLCENTRE-RELATIONSHIP-SCAN", ("MODULE", "Domain")),
        ("CALLCENTRE-VIEW-TABLE-LOCKING", ("PATTERN", "object-placement")),
        ("CALLCENTRE-STD-VIEW-1TO1-CallCentre_DOM_STD_V.Ticket", ("PATTERN", "object-placement")),
        ("CALLCENTRE-BUS-VIEW-SOURCES", ("PATTERN", "object-placement")),
        ("CALLCENTRE-TEMPORAL-CURRENT-ticket", ("PATTERN", "temporal-lifecycle-metadata")),
        ("CALLCENTRE-CAP-002", ("CAPABILITY", "NearestNeighbors")),
        ("CALLCENTRE-CAPABILITY-SCAN", ("CAPABILITY", "NearestNeighbors")),
        ("CALLCENTRE-STRUCT-002", ("PRODUCT", "CallCentre")),
        ("CALLCENTRE-PERF-001", ("PRODUCT", "CallCentre")),
        ("CALLCENTRE-NEWTHING-001", ("PRODUCT", "CallCentre")),
        ("SCANNER:VIEW", ("PATTERN", "object-placement")),
    ],
)
def test_checks_map_to_standard_scopes(check_id, area):
    assert area_for_check(check_id, "CallCentre", REGISTERED) == area


def test_unregistered_module_falls_back_to_product_and_says_so():
    # CallCentre registers no Memory module, so cookbook checks cannot be a MODULE area (VAL-14).
    assert area_for_check("CALLCENTRE-QUERY-001", "CallCentre", REGISTERED) == (
        "PRODUCT",
        "CallCentre",
    )
    product = _areas(_run([_result("CALLCENTRE-QUERY-001", TestStatus.FAILED)]))[
        ("PRODUCT", "CallCentre")
    ]
    assert "not registered in data_product_map" in product.open_gaps
    assert "Memory" in product.open_gaps


def test_module_names_take_the_registered_spelling():
    assert area_for_check("CALLCENTRE-SEM-001", "CallCentre", ["semantic"]) == (
        "MODULE",
        "semantic",
    )


def test_unreadable_map_keeps_module_scopes():
    assert area_for_check("CALLCENTRE-QUERY-001", "CallCentre", None) == ("MODULE", "Memory")


# -- status and confidence (validation.md §4.3) --------------------------------


def test_full_pass_is_strong_with_no_guidance():
    semantic = _areas(_run([_result("CALLCENTRE-SEM-001"), _result("CALLCENTRE-SEM-002")]))[
        ("MODULE", "Semantic")
    ]
    assert (semantic.area_status, semantic.confidence) == ("pass", "strong")
    assert semantic.open_gaps is None and semantic.recommended_action is None


def test_critical_and_error_failures_are_counted_separately_and_weak():
    results = [
        _result("CALLCENTRE-OPS-001", TestStatus.FAILED, TestSeverity.CRITICAL),
        _result("CALLCENTRE-OPS-002", TestStatus.FAILED, TestSeverity.ERROR, repair="Deploy it."),
        _result("CALLCENTRE-OPS-003"),
    ]
    obs = _areas(_run(results))[("MODULE", "Observability")]
    assert (obs.critical_failure_count, obs.error_failure_count) == (1, 1)
    assert (obs.area_status, obs.confidence) == ("fail", "weak")
    assert obs.recommended_action == "Deploy it."


def test_warning_failure_is_partial():
    results = [
        _result("CALLCENTRE-SEM-001"),
        _result("CALLCENTRE-SEM-002", TestStatus.FAILED, TestSeverity.WARNING),
    ]
    assert _areas(_run(results))[("MODULE", "Semantic")].confidence == "partial"


def test_coverage_below_half_is_weak():
    excluded = [
        ExcludedCheck(f"CALLCENTRE-SEM-00{i}", f"Disabled {i}", "SEMANTIC", "Disabled.")
        for i in (2, 3, 4)
    ]
    semantic = _areas(_run([_result("CALLCENTRE-SEM-001")], excluded))[("MODULE", "Semantic")]
    assert (semantic.checks_expected, semantic.checks_ran) == (4, 1)
    assert (semantic.area_status, semantic.confidence) == ("partial", "weak")
    assert "3 of 4 checks disabled" in semantic.open_gaps


def test_registered_module_without_checks_is_no_evidence():
    search = _areas(_run([_result("CALLCENTRE-SEM-001")]))[("MODULE", "Search")]
    assert (search.checks_expected, search.area_status, search.confidence) == (
        0,
        "no-evidence",
        "unknown",
    )
    assert search.open_gaps == "No Trust Engine check covers the Search module."


def test_fully_disabled_area_is_not_validated():
    run = _run([], [ExcludedCheck("SCANNER:VIEW", "View contract scans", "STRUCTURAL", "Off.")])
    view = _areas(run)[("PATTERN", "object-placement")]
    assert (view.area_status, view.confidence) == ("not-validated", "unknown")


def _conformance_violations(area):
    """The standard's VAL-15, VAL-16 and VAL-17 checks, applied to one area."""
    a = area
    failures = a.failed_count + a.error_count
    violations = []
    if (
        a.checks_ran != a.passed_count + a.failed_count + a.error_count
        or a.checks_ran > a.checks_expected
    ):
        violations.append("VAL-15")
    if (
        (a.area_status == "pass" and (a.checks_ran == 0 or a.checks_ran != a.checks_expected))
        or (
            a.confidence == "strong"
            and (a.checks_ran == 0 or a.checks_ran != a.checks_expected or failures)
        )
        or (
            a.area_status == "no-evidence" and (a.checks_expected != 0 or a.confidence != "unknown")
        )
        or (a.area_status == "not-validated" and (a.checks_ran != 0 or a.confidence != "unknown"))
        or (a.area_status == "fail" and failures == 0)
        or (failures and a.area_status != "fail")
        or (
            a.critical_failure_count + a.error_failure_count
            and a.confidence not in ("weak", "unknown")
        )
        or (
            a.confidence == "partial" and a.checks_expected and a.checks_ran * 2 < a.checks_expected
        )
    ):
        violations.append("VAL-16")
    if a.confidence != "strong" and (not a.open_gaps or not a.recommended_action):
        violations.append("VAL-17")
    return violations


def test_every_area_passes_the_standard_conformance_rules():
    statuses = [TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR]
    severities = [
        TestSeverity.INFO,
        TestSeverity.WARNING,
        TestSeverity.ERROR,
        TestSeverity.CRITICAL,
    ]
    families = ["SEM", "OPS", "QUERY", "REL", "VIEW", "TEMPORAL", "CAP", "STRUCT"]
    results = [
        _result(f"CALLCENTRE-{family}-{i:03d}", statuses[i % 3], severities[i % 4])
        for i, family in enumerate(families * 3)
    ]
    excluded = [ExcludedCheck("SCANNER:TEXT", "Free-text scans", "FREE_TEXT", "Off.")]
    for area in build_validation_areas(_run(results, excluded), REGISTERED):
        assert _conformance_violations(area) == [], (area.scope_kind, area.scope_id)


# -- SQL ----------------------------------------------------------------------


def test_publish_sql_appends_the_run_and_its_areas_in_one_request():
    results = [
        _result("CALLCENTRE-SEM-001", TestStatus.FAILED, TestSeverity.ERROR, name="Customer's view")
    ]
    run = _run(results)
    areas = build_validation_areas(run, ["Semantic"])
    sql = validation_publish_sql(run, [], areas, "CallCentre_OBS_STD_T", ["Semantic"])
    statements = sql.split(";\n")
    assert statements[0].startswith("INSERT INTO CallCentre_OBS_STD_T.validation_run (")
    assert all(
        s.startswith("INSERT INTO CallCentre_OBS_STD_T.validation_area (") for s in statements[1:]
    )
    assert "DELETE" not in sql
    assert f"'{PRODUCER_ID}'" in sql and f"'{PAYLOAD_SCHEMA_VERSION}'" in sql
    assert "'MODULE', 'Semantic', 1, 1, 0, 1, 0, 0, 1, 'fail', 'weak'" in sql
    assert "Customer''s view" in sql
    # completed_dts on the run row and on the one area row
    assert sql.count("TIMESTAMP '2026-09-15 01:05:00+00:00'") == 2


def test_run_row_is_wire_schema_2_1_and_never_a_gate():
    run = _run([_result("CALLCENTRE-SEM-001", TestStatus.FAILED, TestSeverity.CRITICAL)])
    row = validation_run_row(run, [], ["Semantic"])
    assert row["payload_schema_version"] == "2.1" and row["producer_id"] == PRODUCER_ID
    assert row["source_format"] == "NATIVE"
    assert row["agent_use_allowed"] == 1  # VAL-02, even though the run is UNTRUSTED
    assert row["trust_status"] == "UNTRUSTED"
    item = json.loads(row["failed_checks_json"])[0]
    assert (item["scope_kind"], item["scope_id"]) == ("MODULE", "Semantic")


def test_area_and_run_share_the_run_id():
    run = _run([_result("CALLCENTRE-SEM-001")])
    sql = validation_publish_sql(run, [], build_validation_areas(run, ["Semantic"]), "Db_OBS_STD_T")
    run_id = validation_run_row(run, [])["run_id"]
    assert len(re.findall(f"'{run_id}'", sql)) == 2


def test_database_name_is_validated():
    run = _run([])
    with pytest.raises(ValueError):
        validation_publish_sql(run, [], [], "Bad Database; DROP")
    assert default_validation_database("CallCentre") == "CallCentre_OBS_STD_T"


# -- publishing ---------------------------------------------------------------


class _Adapter:
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


def test_registered_modules_are_read_or_none():
    assert read_registered_modules(_Adapter(["Semantic", " Domain "]), "CallCentre") == [
        "Semantic",
        "Domain",
    ]
    assert read_registered_modules(_Adapter(fail=True), "CallCentre") is None


def test_publish_validation_writes_one_request():
    adapter = _Adapter(["Domain", "Semantic"])
    database, areas = publish_validation(adapter, _run([_result("CALLCENTRE-SEM-001")]), [])
    assert database == "CallCentre_OBS_STD_T"
    assert [(a.scope_id, a.area_status) for a in areas] == [
        ("Domain", "no-evidence"),
        ("Semantic", "pass"),
    ]
    assert len(adapter.sql) == 1


def test_validate_cli_can_publish_validation(monkeypatch, capsys):
    adapter = _Adapter(["Semantic"])
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

    exit_code = main(["validate", "--prefix", "CallCentre", "--publish-validation"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert (
        "Validation published: CallCentre_OBS_STD_T.validation_run and 1 validation_area rows"
        in captured.out
    )
    assert (
        len(adapter.sql) == 1
        and "INSERT INTO CallCentre_OBS_STD_T.validation_area" in adapter.sql[0]
    )
