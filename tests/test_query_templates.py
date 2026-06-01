from ai_native_data_product_trust_engine.query_templates import (
    bind_sql_template,
    classify_sql_error,
    explain_performance_findings,
    extract_sql_error_evidence,
    is_bounded_sql,
    is_interactive_recipe,
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


def test_is_bounded_sql_detects_parameterised_predicates_and_row_limits():
    assert is_bounded_sql("SELECT TOP 20 * FROM db.table")
    assert is_bounded_sql("SELECT * FROM db.table WHERE business_date >= :start_date")
    assert is_bounded_sql(
        "SELECT * FROM db.table QUALIFY ROW_NUMBER() OVER (ORDER BY score DESC) <= 20"
    )
    assert not is_bounded_sql("SELECT * FROM db.table")


def test_is_interactive_recipe_allows_intentional_batch_patterns():
    assert is_interactive_recipe({"recipe_title": "Customer lookup", "use_case": "Agent answer"})
    assert not is_interactive_recipe(
        {
            "recipe_title": "Full customer extract",
            "use_case": "Offline batch refresh",
        }
    )


def test_explain_performance_findings_extracts_known_risks():
    findings = explain_performance_findings(
        [
            {
                "Explain": (
                    "We do an all-AMPs RETRIEVE step with low confidence. "
                    "The plan includes a product join."
                )
            }
        ]
    )

    issue_codes = {finding["issue_code"] for finding in findings}
    assert "EXPLAIN_ALL_AMP_SCAN" in issue_codes
    assert "EXPLAIN_LOW_CONFIDENCE" in issue_codes
    assert "EXPLAIN_PRODUCT_JOIN" in issue_codes


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

    assert len(results) == 2
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "MISSING_COLUMN"
    assert results[0].sample_rows[0]["missing_column"] == "missing_column"
    assert results[0].sample_rows[0]["parameters"] == ["call_id"]
    assert results[0].sample_rows[0]["referenced_objects"] == ["db.table"]
    assert "SQL object db.table" in results[0].sample_rows[0]["objects_to_examine"]
    assert results[1].status.value == "PASSED"


def test_run_query_template_validations_flags_unbounded_interactive_recipe():
    adapter = StubAdapter(
        recipe_rows=[
            {
                "recipe_id": "QC-002",
                "recipe_title": "Open customer scan",
                "use_case": "Agent analysis",
                "sql_template": "SELECT customer_id FROM db.customer",
            }
        ],
    )

    results = run_query_template_validations("CallCentre", adapter)

    bounds_result = next(result for result in results if "QUERY-BOUNDS" in result.test_case.test_id)
    assert bounds_result.status.value == "FAILED"
    assert bounds_result.test_case.category.value == "PERFORMANCE"
    assert bounds_result.sample_rows[0]["issue_code"] == "UNBOUNDED_INTERACTIVE_RECIPE"
    assert bounds_result.sample_rows[0]["missing_bound_type"] == (
        "parameterised predicate or row-limiting clause"
    )
    assert bounds_result.sample_rows[0]["referenced_objects"] == ["db.customer"]
    assert "SQL object db.customer" in bounds_result.sample_rows[0]["objects_to_examine"]


def test_run_query_template_validations_allows_unbounded_batch_recipe():
    adapter = StubAdapter(
        recipe_rows=[
            {
                "recipe_id": "QC-003",
                "recipe_title": "Full customer extract",
                "use_case": "Offline batch refresh",
                "sql_template": "SELECT customer_id FROM db.customer",
            }
        ],
    )

    results = run_query_template_validations("CallCentre", adapter)

    bounds_result = next(result for result in results if "QUERY-BOUNDS" in result.test_case.test_id)
    assert bounds_result.status.value == "PASSED"
    assert bounds_result.sample_rows[0]["interactive_recipe"] is False


def test_run_query_template_validations_reports_explain_performance_risk():
    adapter = StubAdapter(
        recipe_rows=[
            {
                "recipe_id": "QC-004",
                "recipe_title": "Risky recipe",
                "use_case": "Agent lookup",
                "sql_template": "SELECT * FROM db.customer WHERE customer_id = :customer_id",
            }
        ],
        explain_rows=[
            {
                "Explain": (
                    "We do an all-AMPs RETRIEVE step. This step includes a product join."
                )
            }
        ],
    )

    results = run_query_template_validations("CallCentre", adapter)

    performance_result = next(
        result for result in results if "QUERY-EXPLAIN-PERF" in result.test_case.test_id
    )
    issue_codes = {row["issue_code"] for row in performance_result.sample_rows}
    assert performance_result.status.value == "FAILED"
    assert performance_result.test_case.category.value == "PERFORMANCE"
    assert "EXPLAIN_ALL_AMP_SCAN" in issue_codes
    assert "EXPLAIN_PRODUCT_JOIN" in issue_codes


class StubAdapter:
    def __init__(self, recipe_rows, explain_error=None, explain_rows=None):
        self.recipe_rows = recipe_rows
        self.explain_error = explain_error
        self.explain_rows = explain_rows or [{"Explain": "ok"}]

    def fetch_all(self, sql):
        if sql.startswith("EXPLAIN"):
            if self.explain_error:
                raise self.explain_error
            return self.explain_rows
        return self.recipe_rows
