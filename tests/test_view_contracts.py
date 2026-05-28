from ai_native_data_product_trust_engine.view_contracts import (
    run_view_contract_validations,
    view_contract_test_cases,
)


def test_view_contract_test_cases_include_inventory_sql():
    tests = view_contract_test_cases("CallCentre")

    assert len(tests) == 1
    assert tests[0].test_id == "CALLCENTRE-VIEW-COLUMNS"
    assert "DBC.TablesV" in tests[0].sql
    assert "TableKind = 'V'" in tests[0].sql


def test_run_view_contract_validations_resolves_each_product_view():
    adapter = StubAdapter(
        view_rows=[
            {
                "database_name": "CallCentre_DOM_BUS_V",
                "view_name": "Call_Enriched",
            }
        ]
    )

    results = run_view_contract_validations("CallCentre", adapter)

    assert len(results) == 1
    assert results[0].status.value == "PASSED"
    assert adapter.help_column_sql == [
        (
            'HELP COLUMN dt.* FROM (\n'
            '    SELECT *\n'
            '    FROM "CallCentre_DOM_BUS_V"."Call_Enriched"\n'
            '    WHERE 1 = 0\n'
            ') AS dt'
        )
    ]
    assert results[0].sample_rows[0]["resolved_column_count"] == 1


def test_run_view_contract_validations_reports_compile_failures():
    adapter = StubAdapter(
        view_rows=[
            {
                "database_name": "CallCentre_PRE_BUS_V",
                "view_name": "call_features_current",
            }
        ],
        explain_error=RuntimeError("Column overall_quality_score not found in db.table"),
    )

    results = run_view_contract_validations("CallCentre", adapter)

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "MISSING_COLUMN"
    assert results[0].sample_rows[0]["missing_column"] == "overall_quality_score"


def test_run_view_contract_validations_reports_missing_view_inventory():
    results = run_view_contract_validations("CallCentre", StubAdapter(view_rows=[]))

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "NO_PRODUCT_VIEWS_FOUND"


class StubAdapter:
    def __init__(self, view_rows, explain_error=None):
        self.view_rows = view_rows
        self.explain_error = explain_error
        self.help_column_sql = []

    def fetch_all(self, sql):
        if sql.startswith("HELP COLUMN"):
            self.help_column_sql.append(sql)
            if self.explain_error:
                raise self.explain_error
            return [{"Column Name": "call_id"}]
        return self.view_rows
