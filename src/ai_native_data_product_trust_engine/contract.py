"""Published trust-payload contract shared with the Data Product Browser.

The Trust Engine (producer) writes a ``trust_engine_latest`` row; the Data
Product Browser (consumer) reads it. Neither imports the other — they are
coupled only through this wire format. This module pins that format so the two
repos cannot drift silently:

* ``PAYLOAD_SCHEMA_VERSION`` — bump on any incompatible change to the row
  columns or the JSON blob shapes below.
* ``example_payload()`` — a canonical row built from the *real* serialiser
  (:func:`trust_publish._publish_row`), so the golden fixture can never drift
  from what the Engine actually emits.
* ``contract_fixture()`` — the versioned wrapper written to
  ``contract/trust_payload_example.json`` and vendored into the Browser's tests.

See ``CONTRACT.md`` for the full column/key catalogue and ADR-0001 for the
producer/consumer boundary.
"""

from __future__ import annotations

from ai_native_data_product_trust_engine.models import (
    ExcludedCheck,
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
    _publish_row,
    validation_area_rows,
    validation_run_row,
)

# Bump when the row columns or the failed_checks_json / repair_candidates_json
# object shapes change incompatibly. The Browser asserts the version it supports.
# 2.0: started_at/completed_at (VARCHAR ISO-8601) became started_dts/
# completed_dts (TIMESTAMP(6) WITH TIME ZONE) — canonical temporal names and
# types; latest-run ordering is chronological rather than lexicographic.
# 2.1: additive. Adds the per-area trust map (``validation_area``), producer
# identity on the run record, ``scope_kind``/``scope_id`` on failed-check items,
# and deprecates ``agent_use_allowed`` (always 1; never a decision).
PAYLOAD_SCHEMA_VERSION = "2.1"

# Deterministic timestamps — the module must not call datetime.now() so the
# generated fixture is stable across runs (byte-for-byte golden comparison).
_STARTED_AT = "2026-01-01T00:00:00+00:00"
_COMPLETED_AT = "2026-01-01T00:05:00+00:00"


def _case(
    test_id: str,
    name: str,
    category: TestCategory,
    severity: TestSeverity,
    repair_strategy: str,
) -> TestCase:
    return TestCase(
        test_id=test_id,
        name=name,
        category=category,
        severity=severity,
        sql="SELECT 1;",
        expected_result="Returns zero rows.",
        expected=ExpectedResult.ZERO_ROWS,
        repair_strategy=repair_strategy,
    )


def _example_results() -> list[TestResult]:
    """Representative failed checks spanning the sample_row key shapes the
    Browser relies on (entity/view, product_id, recipe, column, object)."""
    return [
        TestResult(
            test_case=_case(
                "EXAMPLEPRODUCT-SEM-001",
                "Entity metadata references deployed objects",
                TestCategory.SEMANTIC,
                TestSeverity.CRITICAL,
                "Populate entity_metadata.view_name and deploy the referenced BUS_V views.",
            ),
            status=TestStatus.PASSED,
            row_count=0,
            sample_rows=[],
        ),
        TestResult(
            test_case=_case(
                "EXAMPLEPRODUCT-SEM-008",
                "Entity metadata publishes BUS_V view names",
                TestCategory.SEMANTIC,
                TestSeverity.CRITICAL,
                "Populate entity_metadata.view_name and deploy the referenced BUS_V views.",
            ),
            status=TestStatus.FAILED,
            row_count=39,
            sample_rows=[
                {
                    "entity_name": "Agent",
                    "view_name": "ExampleProduct_DOM_BUS_V.Customer_Current",
                    "business_database_name": "ExampleProduct_DOM_BUS_V",
                    "issue_code": "ENTITY_VIEW_NAME_NOT_DEPLOYED",
                    "repair_hint": "Deploy the BUS_V view for agent access.",
                },
                {
                    "entity_name": "AgentInteraction",
                    "view_name": None,
                    "business_database_name": "ExampleProduct_MEM_BUS_V",
                    "issue_code": "ENTITY_VIEW_NAME_MISSING",
                    "repair_hint": "Populate entity_metadata.view_name.",
                },
                {
                    "entity_name": "Call",
                    "view_name": "ExampleProduct_DOM_BUS_V.Order_Current",
                    "business_database_name": "ExampleProduct_DOM_BUS_V",
                    "issue_code": "ENTITY_VIEW_NAME_NOT_DEPLOYED",
                    "repair_hint": "Deploy the BUS_V view for agent access.",
                },
            ],
        ),
        TestResult(
            test_case=_case(
                "EXAMPLEPRODUCT-DISCOVERY-002",
                "Central registry matches orientation metadata",
                TestCategory.SEMANTIC,
                TestSeverity.CRITICAL,
                "Refresh the active_data_product_registry and its manifest_json.",
            ),
            status=TestStatus.FAILED,
            row_count=1,
            sample_rows=[
                {
                    "product_id": "exampleproduct",
                    "issue_code": "MISSING_ORIENTATION_MANIFEST",
                    "issue_detail": "manifest_json is required for the MCP orientation layer.",
                    "repair_hint": "Populate manifest_json with the discovery manifest.",
                }
            ],
        ),
        TestResult(
            test_case=_case(
                "EXAMPLEPRODUCT-QUERY-BOUNDS-BQ-COMP-ALL-HIT-RATE",
                "Interactive recipe is bounded: Quality all-hit rate by category",
                TestCategory.PERFORMANCE,
                TestSeverity.CRITICAL,
                "Add date/key parameters, TOP, SAMPLE, QUALIFY ROW_NUMBER, FETCH FIRST, "
                "or mark the recipe as an intentional batch/exhaustive pattern.",
            ),
            status=TestStatus.FAILED,
            row_count=1,
            sample_rows=[
                {
                    "recipe_id": "BQ-COMP-ALL-HIT-RATE",
                    "recipe_title": "Quality all-hit rate by category",
                    "issue_code": "UNBOUNDED_INTERACTIVE_RECIPE",
                    "interactive_recipe": True,
                    "missing_bound_type": "parameterised predicate or row-limiting clause",
                    "validation_mode": "BOUNDS",
                    "repair_hint": "Add a bound or mark the recipe batch.",
                }
            ],
        ),
        TestResult(
            test_case=_case(
                "EXAMPLEPRODUCT-STRUCT-001",
                "Similar table column names use consistent datatypes",
                TestCategory.STRUCTURAL,
                TestSeverity.WARNING,
                "Align datatype, length, precision and scale for same/similar columns.",
            ),
            status=TestStatus.FAILED,
            row_count=31,
            sample_rows=[
                {
                    "database_name": "ExampleProduct_DOM_STD_T",
                    "table_name": "Customer_H",
                    "column_name": "agent_key",
                    "issue_code": "COLUMN_TYPE_DRIFT",
                    "repair_hint": "Align datatype/length for same-named columns.",
                }
            ],
        ),
        TestResult(
            test_case=_case(
                "EXAMPLEPRODUCT-OPS-002",
                "Observability evidence objects are deployed",
                TestCategory.OPERATIONAL,
                TestSeverity.WARNING,
                "Deploy the Observability evidence tables and Semantic lineage views.",
            ),
            status=TestStatus.FAILED,
            row_count=3,
            sample_rows=[
                {
                    "object_name": "data_lineage",
                    "observability_database": "ExampleProduct_OBS_STD_T",
                    "issue_code": "MISSING_OBSERVABILITY_TABLE",
                    "issue_detail": "Required Observability table is not deployed.",
                    "repair_hint": "Deploy the Observability table.",
                }
            ],
        ),
    ]


def _example_repairs() -> list[RepairCandidate]:
    return [
        RepairCandidate(
            candidate_id="EXAMPLEPRODUCT-STRUCT-001-COLUMN-TYPE-DRIFT",
            issue_code="COLUMN_TYPE_DRIFT",
            summary="Align datatype, length, precision and scale for same/similar columns.",
            mode=RepairMode.PROPOSAL,
            requires_approval=True,
            sql="-- review and align column datatypes",
        ),
        RepairCandidate(
            candidate_id="EXAMPLEPRODUCT-SEM-008-ENTITY-VIEW-NAME",
            issue_code="ENTITY_VIEW_NAME_MISSING",
            summary="Populate entity_metadata.view_name for the flagged entities.",
            mode=RepairMode.PROPOSAL,
            requires_approval=True,
            sql="-- UPDATE entity_metadata SET view_name = ... ",
        ),
    ]


def example_payload() -> dict[str, object]:
    """The canonical ``trust_engine_latest`` row, built from the real serialiser.

    Returns the same dict shape a ``SELECT * FROM <sem>.trust_engine_latest``
    yields: the columns in :data:`trust_publish._PUBLISH_COLUMNS`, with the two
    ``*_json`` columns as JSON strings. The ``*_dts`` timestamps are typed
    columns in the database; the fixture carries them as their canonical
    ISO-8601 string forms because JSON has no timestamp type.
    """
    run = ValidationRun(
        prefix="ExampleProduct",
        started_at=_STARTED_AT,
        completed_at=_COMPLETED_AT,
        results=_example_results(),
    )
    return _publish_row(run, _example_repairs())


def _example_run() -> ValidationRun:
    return ValidationRun(
        prefix="ExampleProduct",
        started_at=_STARTED_AT,
        completed_at=_COMPLETED_AT,
        results=_example_results(),
        excluded_checks=[
            ExcludedCheck(
                check_id="EXAMPLEPRODUCT-CAP-001",
                name="Capability claims match deployed features",
                category="CAPABILITY",
                reason="Disabled by rules config.",
            )
        ],
    )


# Modules the example product declares as deployed. ``domain`` has no checks, so
# the map publishes it as ``no-evidence`` rather than leaving it out (VAL-18).
_EXAMPLE_MODULES = ["domain", "memory", "observability", "semantic"]


def example_validation_run() -> dict[str, object]:
    """The canonical ``validation_run`` row (wire schema 2.1)."""
    row = validation_run_row(_example_run(), _example_repairs())
    # The installed package version varies by environment; pin it so the golden is stable.
    row["producer_version"] = "fixture"
    return row


def example_validation_areas() -> list[dict[str, object]]:
    """The canonical ``validation_area`` rows: the per-area trust map."""
    return validation_area_rows(_example_run(), _EXAMPLE_MODULES)


def contract_fixture() -> dict[str, object]:
    """The versioned wrapper written to ``contract/trust_payload_example.json``
    and vendored into the Browser's test fixtures."""
    return {
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
        "description": (
            "Canonical validation_run + validation_area rows (wire schema 2.1) and the "
            "legacy trust_engine_latest row + JSON blob shapes. Generated by "
            "trust_publish/contract.example_*(); do not hand-edit. See CONTRACT.md."
        ),
        "trust_engine_latest": example_payload(),
        "validation_run": example_validation_run(),
        "validation_area": example_validation_areas(),
    }
