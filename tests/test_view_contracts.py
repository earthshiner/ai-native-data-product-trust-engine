from ai_native_data_product_trust_engine.view_contracts import (
    run_view_contract_validations,
    view_contract_test_cases,
)


def test_view_contract_test_cases_include_inventory_sql():
    tests = view_contract_test_cases("CallCentre")

    assert len(tests) == 3
    assert tests[0].test_id == "CALLCENTRE-VIEW-COLUMNS"
    assert "DBC.TablesV" in tests[0].sql
    assert "TableKind = 'V'" in tests[0].sql
    assert tests[1].test_id == "CALLCENTRE-STD-VIEW-1TO1"
    assert tests[2].test_id == "CALLCENTRE-BUS-VIEW-SOURCES"


def test_run_view_contract_validations_resolves_each_product_view():
    adapter = StubAdapter(
        view_rows=[
            {
                "database_name": "CallCentre_DOM_BUS_V",
                "view_name": "Call_Enriched",
            }
        ],
        std_view_rows=[],
        bus_view_rows=[],
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


def test_run_view_contract_validations_checks_std_view_contract():
    adapter = StubAdapter(
        view_rows=[],
        std_view_rows=[
            {
                "database_name": "CallCentre_DOM_STD_V",
                "view_name": "Call_H",
                "view_text": (
                    "CREATE VIEW CallCentre_DOM_STD_V.Call_H "
                    "(call_id, start_ts) AS "
                    "LOCKING ROW FOR ACCESS "
                    "SELECT call_id, start_ts FROM CallCentre_DOM_STD_T.Call_H;"
                ),
            }
        ],
        bus_view_rows=[],
        column_contract_rows=[],
    )

    results = adapter.run_std_only()

    assert len(results) == 1
    assert results[0].status.value == "PASSED"
    assert "Call_H" in adapter.column_contract_sql[0]


def test_run_view_contract_validations_reports_std_view_logic():
    adapter = StubAdapter(
        view_rows=[],
        std_view_rows=[
            {
                "database_name": "CallCentre_DOM_STD_V",
                "view_name": "Call_H",
                "view_text": (
                    "CREATE VIEW CallCentre_DOM_STD_V.Call_H AS "
                    "SELECT * FROM CallCentre_DOM_STD_T.Call_H WHERE is_current = 1;"
                ),
            }
        ],
        bus_view_rows=[],
        column_contract_rows=[
            {
                "column_id": 2,
                "view_column_name": "topic",
                "table_column_name": "start_ts",
            }
        ],
    )

    results = adapter.run_std_only()
    issue_codes = {row["issue_code"] for row in results[0].sample_rows}

    assert results[0].status.value == "FAILED"
    assert "MISSING_LOCKING_ROW" in issue_codes
    assert "SELECT_STAR" in issue_codes
    assert "MISSING_VIEW_COLUMN_LIST" in issue_codes
    assert "BUSINESS_LOGIC_IN_STD_VIEW" in issue_codes
    assert "STD_VIEW_COLUMN_ORDER_MISMATCH" in issue_codes


def test_run_view_contract_validations_reports_bus_view_selecting_table():
    adapter = StubAdapter(
        view_rows=[],
        std_view_rows=[],
        bus_view_rows=[
            {
                "database_name": "CallCentre_DOM_BUS_V",
                "view_name": "Call_Enriched",
                "view_text": (
                    "CREATE VIEW CallCentre_DOM_BUS_V.Call_Enriched AS "
                    "SELECT call_id FROM CallCentre_DOM_STD_T.Call_H WHERE is_current = 1;"
                ),
            }
        ],
    )

    results = adapter.run_bus_only()

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "BUS_VIEW_SELECTS_TABLE_DIRECTLY"


def test_run_view_contract_validations_reports_compile_failures():
    adapter = StubAdapter(
        view_rows=[
            {
                "database_name": "CallCentre_PRE_BUS_V",
                "view_name": "call_features_current",
            }
        ],
        std_view_rows=[],
        bus_view_rows=[],
        explain_error=RuntimeError("Column overall_quality_score not found in db.table"),
    )

    results = run_view_contract_validations("CallCentre", adapter)

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "MISSING_COLUMN"
    assert results[0].sample_rows[0]["missing_column"] == "overall_quality_score"


def test_run_view_contract_validations_reports_missing_view_inventory():
    results = run_view_contract_validations(
        "CallCentre",
        StubAdapter(view_rows=[], std_view_rows=[], bus_view_rows=[]),
    )

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "NO_PRODUCT_VIEWS_FOUND"


class StubAdapter:
    def __init__(
        self,
        view_rows,
        std_view_rows=None,
        bus_view_rows=None,
        column_contract_rows=None,
        explain_error=None,
    ):
        self.view_rows = view_rows
        self.std_view_rows = std_view_rows or []
        self.bus_view_rows = bus_view_rows or []
        self.column_contract_rows = column_contract_rows or []
        self.explain_error = explain_error
        self.help_column_sql = []
        self.column_contract_sql = []

    def run_std_only(self):
        from ai_native_data_product_trust_engine.view_contracts import (
            _run_standard_view_contract_validations,
        )

        return _run_standard_view_contract_validations("CallCentre", self)

    def run_bus_only(self):
        from ai_native_data_product_trust_engine.view_contracts import (
            _run_business_view_source_validations,
        )

        return _run_business_view_source_validations("CallCentre", self)

    def fetch_all(self, sql):
        if sql.startswith("HELP COLUMN"):
            self.help_column_sql.append(sql)
            if self.explain_error:
                raise self.explain_error
            return [{"Column Name": "call_id"}]
        if "FULL OUTER JOIN table_cols" in sql:
            self.column_contract_sql.append(sql)
            return self.column_contract_rows
        if "COALESCE(RequestText" in sql:
            if "_BUS\\_V" in sql:
                return self.bus_view_rows
            return self.std_view_rows
        return self.view_rows
