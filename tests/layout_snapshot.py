"""Capture every SQL statement the engine builds for a sample prefix.

The default-layout guarantee is: with no declaration and no overrides the engine
emits exactly the SQL it emitted before the Platform Layout Standard was adopted.
``build_snapshot`` gathers the generated checks and the SQL issued by each scanner
against a deterministic recording adapter; ``tests/fixtures/default_layout_sql.json``
holds the output captured from the commit before layout resolution existed.

``extra`` is passed to every generator: ``{}`` for the original code, and
``{"layout": derive_layout(prefix)}`` once layout support exists.
"""

from __future__ import annotations

from dataclasses import fields

from ai_native_data_product_trust_engine import (
    capabilities,
    query_templates,
    relationship_health,
    repairs,
    test_generation,
    text_references,
    trust_publish,
    validation_ddl,
    view_contracts,
)

PREFIX = "SnapProduct"

_VIEW_TEXT = (
    "REPLACE VIEW SnapProduct_DOM_STD_V.Customer_H (customer_key) AS "
    "LOCKING ROW FOR ACCESS SELECT customer_key FROM SnapProduct_DOM_STD_T.Customer_H"
)
_ROW = {
    "database_name": "SnapProduct_DOM_STD_V",
    "view_name": "Customer_H",
    "view_text": _VIEW_TEXT,
    "relationship_id": 1,
    "relationship_name": "Customer to Order",
    "source_database": "SnapProduct_DOM_STD_T",
    "source_table": "Customer_H",
    "source_column": "customer_key",
    "target_database": "SnapProduct_DOM_BUS_V",
    "target_table": "Order_H",
    "target_column": "customer_key",
    "cardinality": "1:M",
    "entity_metadata_id": 7,
    "entity_name": "Customer",
    "table_name": "Customer_H",
    "natural_key_column": "customer_key",
    "current_flag_column": "is_current",
    "deleted_flag_column": "is_deleted",
    "temporal_pattern": "TYPE_2_SCD",
    "recipe_id": "R-1",
    "recipe_title": "Customers",
    "recipe_description": "Customer lookup",
    "use_case": "Lookup",
    "performance_notes": "",
    "complexity": "LOW",
    "is_batch": 0,
    "sql_template": "SELECT TOP 10 * FROM SnapProduct_DOM_BUS_V.Customer_Current",
}


class RecordingAdapter:
    """Returns one canned row for every query and records the SQL it was given."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def fetch_all(self, sql: str) -> list[dict[str, object]]:
        self.sql.append(sql)
        return [dict(_ROW)]

    def fetch_all_with_session_setup(self, sql, setup_sql=None, teardown_sql=None):
        self.sql.append(sql)
        return [dict(_ROW)]

    def execute(self, sql: str) -> None:
        self.sql.append(sql)


def _case_dict(case) -> dict[str, object]:
    return {
        field.name: (getattr(case, field.name).value if hasattr(getattr(case, field.name), "value") else getattr(case, field.name))
        for field in fields(case)
    }


def _scan(name: str, runner, extra: dict[str, object]) -> dict[str, object]:
    adapter = RecordingAdapter()
    try:
        results = runner(PREFIX, adapter, **extra)
        outcome = [[r.test_case.test_id, r.status.value, r.row_count] for r in results]
    except Exception as exc:  # noqa: BLE001 - the failure itself is part of the snapshot
        outcome = [f"EXC {type(exc).__name__}: {exc}"]
    return {"sql": adapter.sql, "results": outcome}


def build_snapshot(extra: dict[str, object] | None = None) -> dict[str, object]:
    extra = dict(extra or {})
    cases = {
        "metadata": test_generation.generate_metadata_tests(PREFIX, **extra),
        "capability": capabilities.capability_test_cases(PREFIX, **extra),
        "query": query_templates.query_template_test_cases(PREFIX, **extra),
        "relationship": relationship_health.relationship_health_test_cases(PREFIX, **extra),
        "text": text_references.text_reference_test_cases(PREFIX, **extra),
        "view": view_contracts.view_contract_test_cases(PREFIX, **extra),
    }
    snapshot: dict[str, object] = {
        "cases": {key: [_case_dict(case) for case in value] for key, value in cases.items()},
        "scans": {
            "capability": _scan("capability", capabilities.run_capability_validations, extra),
            "query": _scan("query", query_templates.run_query_template_validations, extra),
            "relationship": _scan(
                "relationship", relationship_health.run_relationship_health_validations, extra
            ),
            "text": _scan("text", text_references.run_text_reference_validations, extra),
            "view": _scan("view", view_contracts.run_view_contract_validations, extra),
        },
        "defaults": {
            "trust_table": trust_publish.default_trust_table(PREFIX, **extra),
            "trust_view": trust_publish.default_trust_view(PREFIX, **extra),
            "validation_database": trust_publish.default_validation_database(PREFIX, **extra),
            "validation_view_database": validation_ddl.default_validation_view_database(
                PREFIX, **extra
            ),
            "validation_ddl": validation_ddl.validation_ddl(PREFIX, **extra),
            "trust_table_ddl": trust_publish.trust_table_ddl(PREFIX),
        },
        "governed_access": [
            relationship_health._governed_access_database(name)
            if not extra
            else extra["layout"].governed_access_database(name)
            for name in ("SnapProduct_DOM_STD_T", "SnapProduct_DOM_STD_V", "Other_DB")
        ],
        "repair_database": [
            repairs._repair_database_name(name) if not extra else extra["layout"].storage_database_for(name)
            for name in ("SnapProduct_SEM_STD_V", "SnapProduct_MEM_STD_T")
        ],
    }
    return snapshot
