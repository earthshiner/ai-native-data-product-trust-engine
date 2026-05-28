from ai_native_data_product_trust_engine.capabilities import (
    CapabilityStatus,
    discover_fallback_embedding_capability,
    discover_native_vector_capability,
    run_capability_validations,
)


def test_discover_native_vector_capability_marks_unavailable_without_evidence():
    adapter = StubAdapter(native_rows=[], fallback_rows=[])

    capability = discover_native_vector_capability("CallCentre", adapter)

    assert capability.name == "NATIVE_VECTOR"
    assert capability.status == CapabilityStatus.UNAVAILABLE


def test_discover_fallback_embedding_capability_marks_available_with_embedding_columns():
    adapter = StubAdapter(
        native_rows=[],
        fallback_rows=[
            {
                "DatabaseName": "CallCentre_SCH_STD_T",
                "TableName": "call_embedding",
                "ColumnName": "embedding_value",
                "ColumnType": "F",
            }
        ],
    )

    capability = discover_fallback_embedding_capability("CallCentre", adapter)

    assert capability.name == "FALLBACK_EMBEDDING"
    assert capability.status == CapabilityStatus.AVAILABLE


def test_run_capability_validations_flags_native_vector_references_when_unavailable():
    adapter = StubAdapter(
        native_rows=[],
        fallback_rows=[{"TableName": "call_embedding", "ColumnName": "embedding_value"}],
        reference_rows=[
            {
                "recipe_id": "QC-SEARCH-001",
                "recipe_title": "Semantic similarity search",
                "recipe_description": "Uses native VECTOR search.",
                "use_case": None,
                "performance_notes": None,
                "sql_template": "SELECT * FROM TD_VECTORDISTANCE(ON t)",
            }
        ],
    )

    inventory_result, alignment_result = run_capability_validations("CallCentre", adapter)

    assert inventory_result.status.value == "PASSED"
    assert alignment_result.status.value == "FAILED"
    assert alignment_result.sample_rows[0]["issue_code"] == "UNSUPPORTED_CAPABILITY"
    assert alignment_result.sample_rows[0]["capability"] == "NATIVE_VECTOR"


class StubAdapter:
    def __init__(self, native_rows, fallback_rows, reference_rows=None):
        self.native_rows = native_rows
        self.fallback_rows = fallback_rows
        self.reference_rows = reference_rows or []

    def fetch_all(self, sql):
        if "Query_Cookbook" in sql:
            return self.reference_rows
        if "UPPER(ColumnType) IN ('VECTOR', 'VE')" in sql:
            return self.native_rows
        if "LIKE '%EMBED%'" in sql:
            return self.fallback_rows
        return []
