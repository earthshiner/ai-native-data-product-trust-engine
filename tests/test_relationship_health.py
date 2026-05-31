from ai_native_data_product_trust_engine.relationship_health import (
    relationship_health_test_cases,
    run_relationship_cardinality_validations,
    run_relationship_health_validations,
    run_relationship_orphan_validations,
    run_temporal_current_validations,
)


RELATIONSHIP_ROW = {
    "relationship_id": 1,
    "relationship_name": "Call to Customer",
    "source_database": "CallCentre_DOM_STD_T",
    "source_table": "Call_H",
    "source_column": "customer_id",
    "target_database": "CallCentre_DOM_STD_T",
    "target_table": "Customer_H",
    "target_column": "customer_id",
    "cardinality": "M:1",
}

TEMPORAL_ROW = {
    "entity_metadata_id": 1,
    "entity_name": "Customer",
    "database_name": "CallCentre_DOM_STD_T",
    "table_name": "Customer_H",
    "view_name": "Customer_Current",
    "natural_key_column": "customer_id",
    "current_flag_column": "is_current",
    "deleted_flag_column": "is_deleted",
    "temporal_pattern": "TYPE_2_SCD",
}


def test_relationship_health_test_cases_are_listed_for_cli_generation():
    tests = relationship_health_test_cases("CallCentre")

    assert [test.test_id for test in tests] == [
        "CALLCENTRE-REL-ORPHANS",
        "CALLCENTRE-REL-CARDINALITY",
        "CALLCENTRE-TEMPORAL-CURRENT",
    ]
    assert tests[0].category.value == "DATA_QUALITY"


def test_run_relationship_orphan_validations_reports_bounded_orphans():
    adapter = RelationshipStubAdapter(
        relationship_rows=[RELATIONSHIP_ROW],
        orphan_rows=[
            {
                "relationship_name": "Call to Customer",
                "issue_code": "SOURCE_TO_TARGET_ORPHAN",
                "orphan_count": 2,
                "orphan_rate_percent": 1.5,
            }
        ],
    )

    results = run_relationship_orphan_validations("CallCentre", adapter)

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "SOURCE_TO_TARGET_ORPHAN"
    assert "Call-to-Customer" in results[0].test_case.test_id
    assert adapter.executed_sql[1].startswith("WITH source_sample AS")


def test_run_relationship_cardinality_validations_reports_declared_mismatch():
    adapter = RelationshipStubAdapter(
        relationship_rows=[RELATIONSHIP_ROW],
        cardinality_rows=[
            {
                "relationship_name": "Call to Customer",
                "issue_code": "CARDINALITY_TARGET_NOT_UNIQUE",
                "duplicate_key_count": 1,
            }
        ],
    )

    results = run_relationship_cardinality_validations("CallCentre", adapter)

    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "CARDINALITY_TARGET_NOT_UNIQUE"
    assert "M:1" in adapter.executed_sql[1]


def test_run_temporal_current_validations_reports_duplicate_current_records():
    adapter = RelationshipStubAdapter(
        temporal_rows=[TEMPORAL_ROW],
        temporal_duplicate_rows=[
            {
                "entity_name": "Customer",
                "issue_code": "DUPLICATE_CURRENT_RECORD",
                "duplicate_key_count": 1,
            }
        ],
        view_rows=[{"view_text": "SELECT * FROM Customer_H WHERE is_current = 1"}],
    )

    results = run_temporal_current_validations("CallCentre", adapter)

    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "DUPLICATE_CURRENT_RECORD"


def test_run_temporal_current_validations_reports_missing_current_view_filter():
    adapter = RelationshipStubAdapter(
        temporal_rows=[TEMPORAL_ROW],
        temporal_duplicate_rows=[],
        view_rows=[{"view_text": "SELECT * FROM Customer_H"}],
    )

    results = run_temporal_current_validations("CallCentre", adapter)

    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "CURRENT_VIEW_MISSING_CURRENT_FILTER"


def test_run_relationship_health_validations_combines_all_check_families():
    adapter = RelationshipStubAdapter(
        relationship_rows=[RELATIONSHIP_ROW],
        temporal_rows=[TEMPORAL_ROW],
        orphan_rows=[],
        cardinality_rows=[],
        temporal_duplicate_rows=[],
        view_rows=[{"view_text": "SELECT * FROM Customer_H WHERE is_current = 1"}],
    )

    results = run_relationship_health_validations("CallCentre", adapter)

    assert len(results) == 3
    assert all(result.status.value == "PASSED" for result in results)


class RelationshipStubAdapter:
    def __init__(
        self,
        relationship_rows=None,
        temporal_rows=None,
        orphan_rows=None,
        cardinality_rows=None,
        temporal_duplicate_rows=None,
        view_rows=None,
    ):
        self.relationship_rows = relationship_rows or []
        self.temporal_rows = temporal_rows or []
        self.orphan_rows = orphan_rows or []
        self.cardinality_rows = cardinality_rows or []
        self.temporal_duplicate_rows = temporal_duplicate_rows or []
        self.view_rows = view_rows or []
        self.executed_sql = []

    def fetch_all(self, sql):
        self.executed_sql.append(sql)
        if "FROM CallCentre_SEM_STD_V.table_relationship" in sql:
            return self.relationship_rows
        if "FROM CallCentre_SEM_STD_V.entity_metadata" in sql:
            return self.temporal_rows
        if "SOURCE_TO_TARGET_ORPHAN" in sql:
            return self.orphan_rows
        if "CARDINALITY_SOURCE_NOT_UNIQUE" in sql:
            return self.cardinality_rows
        if "DUPLICATE_CURRENT_RECORD" in sql:
            return self.temporal_duplicate_rows
        if "FROM DBC.TablesV" in sql:
            return self.view_rows
        return []
