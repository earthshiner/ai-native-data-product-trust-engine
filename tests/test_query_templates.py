from ai_native_data_product_trust_engine.query_templates import (
    bind_sql_template,
    classify_sql_error,
    extract_sql_error_evidence,
    run_query_template_validations,
)


def test_bind_sql_template_replaces_named_parameters_once_per_occurrence():
    bound = bind_sql_template(
        "SELECT * FROM db.table WHERE business_date BETWEEN :start_date AND :end_date "
        "QUALIFY ROW_NUMBER() OVER (ORDER BY score DESC) <= :top_k"
    )

    assert bound.parameters == ("start_date", "end_date", "top_k")
    assert "DATE '2025-01-01'" in bound.sql
    assert bound.sql.endswith("<= 20")


def test_classify_sql_error_detects_missing_column():
    assert classify_sql_error("Column overall_quality_score not found in db.table") == (
        "MISSING_COLUMN"
    )
    assert classify_sql_error("Column/Parameter 'db.table.start_ts' does not exist.") == (
        "MISSING_COLUMN"
    )


def test_extract_sql_error_evidence_returns_missing_object_name():
    evidence = extract_sql_error_evidence(
        "Object 'CallCentre_DOM_BUS_V.Call_H' does not exist."
    )

    assert evidence == {
        "issue_code": "MISSING_OBJECT",
        "repair_hint": (
            "Update the SQL template to a deployed object, create the missing view, or quarantine "
            "the recipe."
        ),
        "missing_object": "CallCentre_DOM_BUS_V.Call_H",
    }


def test_extract_sql_error_evidence_marks_native_vector_capability():
    evidence = extract_sql_error_evidence(
        "Object 'CallCentre_SCH_STD_V.call_embedding' does not exist.",
        "SELECT * FROM TD_VECTORDISTANCE(ON t AS TargetTable)",
    )

    assert evidence == {
        "issue_code": "UNSUPPORTED_CAPABILITY",
        "repair_hint": "Use a capability-compatible recipe variant or mark the native capability unavailable.",
        "capability": "NATIVE_VECTOR",
        "unsupported_feature": "TD_VECTORDISTANCE",
    }


def test_run_query_template_validations_reports_recipe_failures():
    adapter = StubAdapter(
        recipe_rows=[
            {
                "recipe_id": "QC-001",
                "recipe_title": "Broken recipe",
                "sql_template": "SELECT missing_column FROM db.table WHERE id = :call_id",
            }
        ],
        explain_error=RuntimeError("Column missing_column not found in db.table"),
    )

    results = run_query_template_validations("CallCentre", adapter)

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "MISSING_COLUMN"
    assert results[0].sample_rows[0]["missing_column"] == "missing_column"
    assert results[0].sample_rows[0]["parameters"] == ["call_id"]


class StubAdapter:
    def __init__(self, recipe_rows, explain_error=None):
        self.recipe_rows = recipe_rows
        self.explain_error = explain_error

    def fetch_all(self, sql):
        if sql.startswith("EXPLAIN"):
            if self.explain_error:
                raise self.explain_error
            return [{"Explain": "ok"}]
        return self.recipe_rows
