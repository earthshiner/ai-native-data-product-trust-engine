"""Per-area trust map: scope resolution, status/confidence rules, publishing."""

import pytest

from ai_native_data_product_trust_engine.models import (
    ExcludedCheck,
    ExpectedResult,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.trust_map import build_trust_map, scope_for_check
from ai_native_data_product_trust_engine.trust_publish import (
    PRODUCER_ID,
    declared_modules,
    publish_validation_result,
    validation_area_rows,
    validation_run_id,
    validation_run_insert_sql,
    validation_run_row,
)


@pytest.mark.parametrize(
    ("check_id", "expected"),
    [
        ("EXAMPLEPRODUCT-SEM-008", ("MODULE", "semantic")),
        ("EXAMPLEPRODUCT-DISCOVERY-002", ("MODULE", "semantic")),
        ("EXAMPLEPRODUCT-REL-ORPHANS", ("MODULE", "semantic")),
        ("EXAMPLEPRODUCT-OPS-004", ("MODULE", "observability")),
        ("EXAMPLEPRODUCT-QUERY-EXPLAIN-BQ-1", ("MODULE", "memory")),
        ("EXAMPLEPRODUCT-QUERY-BOUNDS-BQ-1", ("MODULE", "memory")),
        ("EXAMPLEPRODUCT-PERF-001", ("MODULE", "memory")),
        ("EXAMPLEPRODUCT-TEMPORAL-CURRENT-AGENT", ("PATTERN", "temporal-lifecycle-metadata")),
        ("EXAMPLEPRODUCT-STD-VIEW-1TO1-DB.V", ("PATTERN", "object-placement")),
        ("EXAMPLEPRODUCT-STD-TABLE-VIEW-COVERAGE", ("PATTERN", "object-placement")),
        ("EXAMPLEPRODUCT-BUS-VIEW-SOURCES-DB.V", ("PATTERN", "object-placement")),
        ("EXAMPLEPRODUCT-VIEW-COLUMNS", ("PATTERN", "object-placement")),
        ("EXAMPLEPRODUCT-STRUCT-001", ("PRODUCT", "ExampleProduct")),
        ("EXAMPLEPRODUCT-CAP-002", ("PRODUCT", "ExampleProduct")),
        ("EXAMPLEPRODUCT-SOMETHING-NEW-001", ("PRODUCT", "ExampleProduct")),
    ],
)
def test_scope_follows_the_owning_module_or_pattern(check_id, expected):
    assert scope_for_check(check_id, "ExampleProduct") == expected


def test_a_clean_fully_covered_area_is_pass_and_strong_without_guidance():
    area = _only(build_trust_map(_run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED)])))

    assert (area.area_status, area.confidence) == ("pass", "strong")
    assert (area.checks_expected, area.checks_ran) == (1, 1)
    assert area.open_gaps is None and area.recommended_action is None


def test_critical_failure_is_fail_weak_with_guidance():
    area = _only(
        build_trust_map(
            _run(
                [
                    _result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED),
                    _result("EXAMPLEPRODUCT-SEM-002", TestStatus.FAILED, TestSeverity.CRITICAL),
                ]
            )
        )
    )

    assert (area.area_status, area.confidence) == ("fail", "weak")
    assert (area.critical_failure_count, area.failed_count) == (1, 1)
    assert area.open_gaps and area.recommended_action


def test_warning_only_failure_is_fail_but_partial_not_weak():
    area = _only(
        build_trust_map(_run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.FAILED, TestSeverity.WARNING)]))
    )

    assert (area.area_status, area.confidence) == ("fail", "partial")
    assert area.critical_failure_count == 0 and area.error_failure_count == 0


def test_errored_check_counts_as_ran_and_failed():
    area = _only(
        build_trust_map(_run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.ERROR, TestSeverity.ERROR)]))
    )

    assert area.area_status == "fail"
    assert (area.checks_ran, area.error_count, area.error_failure_count) == (1, 1, 1)


def test_excluded_checks_lower_coverage_to_partial():
    run = _run(
        [_result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED), _result("EXAMPLEPRODUCT-SEM-002", TestStatus.PASSED)],
        excluded=[ExcludedCheck("EXAMPLEPRODUCT-SEM-003", "Skipped", "SEMANTIC", "disabled")],
    )

    area = _only(build_trust_map(run))

    assert (area.checks_expected, area.checks_ran) == (3, 2)
    assert (area.area_status, area.confidence) == ("partial", "partial")
    assert "excluded" in area.open_gaps


def test_coverage_below_half_is_weak_even_when_everything_that_ran_passed():
    run = _run(
        [_result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED)],
        excluded=[ExcludedCheck(f"EXAMPLEPRODUCT-SEM-00{n}", "Skipped", "SEMANTIC", "x") for n in (2, 3)],
    )

    area = _only(build_trust_map(run))

    assert (area.area_status, area.confidence) == ("partial", "weak")


def test_all_checks_excluded_is_not_validated_and_unknown():
    run = _run([], excluded=[ExcludedCheck("EXAMPLEPRODUCT-SEM-001", "Skipped", "SEMANTIC", "x")])

    area = _only(build_trust_map(run))

    assert (area.area_status, area.confidence) == ("not-validated", "unknown")
    assert area.open_gaps and area.recommended_action


def test_declared_module_with_no_checks_is_published_as_no_evidence():
    entries = build_trust_map(
        _run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED)]), declared_modules=["domain", "semantic"]
    )

    by_id = {(e.scope_kind, e.scope_id): e for e in entries}
    domain = by_id[("MODULE", "domain")]
    assert (domain.area_status, domain.confidence) == ("no-evidence", "unknown")
    assert domain.checks_expected == 0 and domain.open_gaps and domain.recommended_action
    assert by_id[("MODULE", "semantic")].area_status == "pass"


def test_every_failed_check_scope_resolves_to_an_entry_in_the_same_run():
    run = _run(
        [
            _result("EXAMPLEPRODUCT-SEM-001", TestStatus.FAILED, TestSeverity.CRITICAL),
            _result("EXAMPLEPRODUCT-OPS-002", TestStatus.FAILED),
            _result("EXAMPLEPRODUCT-STRUCT-001", TestStatus.FAILED),
        ]
    )

    keys = {(e.scope_kind, e.scope_id) for e in build_trust_map(run)}

    for result in run.results:
        assert scope_for_check(result.test_case.test_id, "ExampleProduct") in keys


def test_area_counts_satisfy_the_standard_invariants():
    run = _run(
        [
            _result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED),
            _result("EXAMPLEPRODUCT-SEM-002", TestStatus.FAILED, TestSeverity.ERROR),
            _result("EXAMPLEPRODUCT-SEM-003", TestStatus.ERROR, TestSeverity.CRITICAL),
        ],
        excluded=[ExcludedCheck("EXAMPLEPRODUCT-SEM-004", "Skipped", "SEMANTIC", "x")],
    )

    for area in build_trust_map(run):
        # VAL-15
        assert area.checks_ran == area.passed_count + area.failed_count + area.error_count
        assert area.checks_ran <= area.checks_expected
        # VAL-16
        if area.area_status in {"no-evidence", "not-validated"}:
            assert area.confidence == "unknown"
        if area.confidence == "strong":
            assert area.area_status == "pass"
        # VAL-17
        if area.confidence != "strong":
            assert area.open_gaps and area.recommended_action


def test_agent_use_allowed_is_always_published_as_go():
    run = _run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.FAILED, TestSeverity.CRITICAL)])

    row = validation_run_row(run, [])

    assert row["trust_status"] == "UNTRUSTED"
    assert row["agent_use_allowed"] == 1


def test_run_row_carries_producer_identity_and_schema_version():
    row = validation_run_row(_run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED)]), [])

    assert row["producer_id"] == PRODUCER_ID
    assert row["payload_schema_version"] == "2.1"
    assert row["source_format"] == "NATIVE"
    assert row["run_id"] == validation_run_id(_run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED)]))
    assert row["evidence_expires_dts"] is None


def test_area_rows_share_the_run_id_and_completion_instant():
    run = _run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED)])

    rows = validation_area_rows(run, ["domain"])

    assert {r["run_id"] for r in rows} == {validation_run_id(run)}
    assert {r["completed_dts"] for r in rows} == {run.completed_at}
    assert {r["producer_id"] for r in rows} == {PRODUCER_ID}


def test_run_insert_targets_observability_validation_run_with_typed_timestamps():
    sql = validation_run_insert_sql(_run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED)]), [])

    assert sql.startswith("INSERT INTO ExampleProduct_OBS_STD_T.validation_run")
    assert "TIMESTAMP '2026-06-01 10:00:00+10:00'" in sql
    assert "'2.1'" in sql and f"'{PRODUCER_ID}'" in sql


def test_publish_appends_the_run_then_one_statement_per_area():
    adapter = _Recorder()
    run = _run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.PASSED), _result("EXAMPLEPRODUCT-OPS-001", TestStatus.PASSED)])

    database, area_count = publish_validation_result(adapter, run, [], modules=["domain"])

    assert database == "ExampleProduct_OBS_STD_T"
    assert area_count == 3  # semantic, observability, domain(no-evidence)
    assert adapter.sql[0].startswith("INSERT INTO ExampleProduct_OBS_STD_T.validation_run")
    assert len(adapter.sql) == 1 + area_count
    assert all(s.startswith("INSERT INTO ExampleProduct_OBS_STD_T.validation_area") for s in adapter.sql[1:])


def test_publish_escapes_quotes_in_guidance_text():
    adapter = _Recorder()
    run = _run([_result("EXAMPLEPRODUCT-SEM-001", TestStatus.FAILED, TestSeverity.ERROR, name="Owner's view")])

    publish_validation_result(adapter, run, [])

    assert "Owner''s view" in adapter.sql[1]


def test_publish_rejects_an_unsafe_database_name():
    with pytest.raises(ValueError, match="ADPTrust.InvalidTrustTable"):
        publish_validation_result(_Recorder(), _run([]), [], "Bad.Name; DROP")


def test_declared_modules_reads_the_data_product_map_and_soft_fails():
    class Reads:
        def fetch_all(self, sql):
            assert "ExampleProduct_SEM_STD_V.data_product_map" in sql
            return [{"module_name": "SEMANTIC"}, {"MODULE_NAME": "Memory"}, {"module_name": "semantic"}]

    class Broken:
        def fetch_all(self, sql):
            raise RuntimeError("no such table")

    assert declared_modules(Reads(), "ExampleProduct") == ["memory", "semantic"]
    assert declared_modules(Broken(), "ExampleProduct") == []


def _only(entries):
    assert len(entries) == 1, entries
    return entries[0]


def _run(results, excluded=None):
    return ValidationRun(
        prefix="ExampleProduct",
        started_at="2026-06-01T10:00:00+10:00",
        completed_at="2026-06-01T10:00:01+10:00",
        results=results,
        excluded_checks=excluded or [],
    )


def _result(test_id, status, severity=TestSeverity.WARNING, name="Metadata stays current"):
    return TestResult(
        test_case=TestCase(
            test_id=test_id,
            name=name,
            category=TestCategory.SEMANTIC,
            severity=severity,
            sql="SELECT 1;",
            expected_result="No stale metadata rows.",
            expected=ExpectedResult.ZERO_ROWS,
            repair_strategy="Refresh metadata.",
        ),
        status=status,
        row_count=0 if status == TestStatus.PASSED else 1,
    )


class _Recorder:
    def __init__(self):
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)
