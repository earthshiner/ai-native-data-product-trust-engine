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
from ai_native_data_product_trust_engine.trust_publish import _publish_row

# Bump when the row columns or the failed_checks_json / repair_candidates_json
# object shapes change incompatibly. The Browser asserts the version it supports.
PAYLOAD_SCHEMA_VERSION = "1.0"

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
                "CALLCENTRE-SEM-001",
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
                "CALLCENTRE-SEM-008",
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
                    "view_name": "CallCentre_DOM_BUS_V.Agent_Current",
                    "business_database_name": "CallCentre_DOM_BUS_V",
                    "issue_code": "ENTITY_VIEW_NAME_NOT_DEPLOYED",
                    "repair_hint": "Deploy the BUS_V view for agent access.",
                },
                {
                    "entity_name": "AgentInteraction",
                    "view_name": None,
                    "business_database_name": "CallCentre_MEM_BUS_V",
                    "issue_code": "ENTITY_VIEW_NAME_MISSING",
                    "repair_hint": "Populate entity_metadata.view_name.",
                },
                {
                    "entity_name": "Call",
                    "view_name": "CallCentre_DOM_BUS_V.Call_Current",
                    "business_database_name": "CallCentre_DOM_BUS_V",
                    "issue_code": "ENTITY_VIEW_NAME_NOT_DEPLOYED",
                    "repair_hint": "Deploy the BUS_V view for agent access.",
                },
            ],
        ),
        TestResult(
            test_case=_case(
                "CALLCENTRE-DISCOVERY-002",
                "Central registry matches orientation metadata",
                TestCategory.SEMANTIC,
                TestSeverity.CRITICAL,
                "Refresh the active_data_product_registry and its manifest_json.",
            ),
            status=TestStatus.FAILED,
            row_count=1,
            sample_rows=[
                {
                    "product_id": "callcentre",
                    "issue_code": "MISSING_ORIENTATION_MANIFEST",
                    "issue_detail": "manifest_json is required for the MCP orientation layer.",
                    "repair_hint": "Populate manifest_json with the discovery manifest.",
                }
            ],
        ),
        TestResult(
            test_case=_case(
                "CALLCENTRE-QUERY-BOUNDS-BQ-COMP-ALL-HIT-RATE",
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
                "CALLCENTRE-STRUCT-001",
                "Similar table column names use consistent datatypes",
                TestCategory.STRUCTURAL,
                TestSeverity.WARNING,
                "Align datatype, length, precision and scale for same/similar columns.",
            ),
            status=TestStatus.FAILED,
            row_count=31,
            sample_rows=[
                {
                    "database_name": "CallCentre_DOM_STD_T",
                    "table_name": "Agent_H",
                    "column_name": "agent_key",
                    "issue_code": "COLUMN_TYPE_DRIFT",
                    "repair_hint": "Align datatype/length for same-named columns.",
                }
            ],
        ),
        TestResult(
            test_case=_case(
                "CALLCENTRE-OPS-002",
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
                    "observability_database": "CallCentre_OBS_STD_T",
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
            candidate_id="CALLCENTRE-STRUCT-001-COLUMN-TYPE-DRIFT",
            issue_code="COLUMN_TYPE_DRIFT",
            summary="Align datatype, length, precision and scale for same/similar columns.",
            mode=RepairMode.PROPOSAL,
            requires_approval=True,
            sql="-- review and align column datatypes",
        ),
        RepairCandidate(
            candidate_id="CALLCENTRE-SEM-008-ENTITY-VIEW-NAME",
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
    ``*_json`` columns as JSON strings.
    """
    run = ValidationRun(
        prefix="CallCentre",
        started_at=_STARTED_AT,
        completed_at=_COMPLETED_AT,
        results=_example_results(),
    )
    return _publish_row(run, _example_repairs())


def contract_fixture() -> dict[str, object]:
    """The versioned wrapper written to ``contract/trust_payload_example.json``
    and vendored into the Browser's test fixtures."""
    return {
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
        "description": (
            "Canonical trust_engine_latest row + JSON blob shapes. Generated by "
            "trust_publish/contract.example_payload(); do not hand-edit. See CONTRACT.md."
        ),
        "trust_engine_latest": example_payload(),
    }
