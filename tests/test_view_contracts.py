from ai_native_data_product_trust_engine.view_contracts import (
    run_view_contract_validations,
    view_contract_test_cases,
)


def test_view_contract_test_cases_include_inventory_sql():
    tests = view_contract_test_cases("CallCentre")

    assert len(tests) == 6
    assert tests[0].test_id == "CALLCENTRE-VIEW-COLUMNS"
    assert "DBC.TablesV" in tests[0].sql
    assert "TableKind = 'V'" in tests[0].sql
    assert tests[1].test_id == "CALLCENTRE-STD-VIEW-1TO1"
    assert tests[2].test_id == "CALLCENTRE-STD-TABLE-VIEW-COVERAGE"
    assert "MISSING_STANDARD_LOCKING_VIEW" in tests[2].sql
    assert "expected_view_database_name" in tests[2].sql
    assert tests[3].test_id == "CALLCENTRE-STD-VIEW-COLUMN-CONTRACT"
    assert "ColumnId order" in tests[3].expected_result
    assert tests[4].test_id == "CALLCENTRE-BUS-VIEW-SOURCES"
    assert tests[5].test_id == "CALLCENTRE-VIEW-TABLE-LOCKING"


def test_view_contract_inventory_sql_excludes_backup_objects():
    tests = view_contract_test_cases("CallCentre")

    for test in tests:
        if "DBC.TablesV" in test.sql:
            assert "_BKP" in test.sql
            assert "_BK" in test.sql
            assert "deployment_status" in test.sql
            assert "data_product_map module_scope" in test.sql


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
        locking_view_rows=[],
        table_view_coverage_rows=[],
    )

    results = run_view_contract_validations("CallCentre", adapter)

    assert len(results) == 2
    assert results[0].status.value == "PASSED"
    assert results[1].status.value == "PASSED"
    assert results[1].test_case.test_id == "CALLCENTRE-STD-TABLE-VIEW-COVERAGE"
    assert adapter.help_column_sql == [
        (
            'HELP COLUMN dt01.* FROM (\n'
            '    SELECT viw.*\n'
            '    FROM "CallCentre_DOM_BUS_V"."Call_Enriched" AS viw\n'
            '    WHERE 1 = 2\n'
            ') AS dt01'
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
        locking_view_rows=[],
        table_view_coverage_rows=[],
    )

    results = adapter.run_std_only()

    assert len(results) == 1
    assert results[0].status.value == "PASSED"


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
        column_contract_rows=[],
        locking_view_rows=[],
        table_view_coverage_rows=[],
    )

    results = adapter.run_std_only()
    issue_codes = {row["issue_code"] for row in results[0].sample_rows}

    assert results[0].status.value == "FAILED"
    assert "MISSING_LOCKING_ROW" in issue_codes
    assert "SELECT_STAR" in issue_codes
    assert "MISSING_VIEW_COLUMN_LIST" in issue_codes
    assert "BUSINESS_LOGIC_IN_STD_VIEW" in issue_codes


def test_run_view_contract_validations_reports_std_view_column_contract_drift():
    adapter = StubAdapter(
        view_rows=[],
        std_view_rows=[
            {
                "database_name": "CallCentre_DOM_STD_V",
                "view_name": "Call_H",
                "view_text": (
                    "CREATE VIEW CallCentre_DOM_STD_V.Call_H "
                    "(call_id, topic) AS "
                    "LOCKING ROW FOR ACCESS "
                    "SELECT call_id, topic FROM CallCentre_DOM_STD_T.Call_H;"
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
        locking_view_rows=[],
        table_view_coverage_rows=[],
    )

    results = adapter.run_columns_only()

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].test_case.test_id.startswith(
        "CALLCENTRE-STD-VIEW-COLUMN-CONTRACT-"
    )
    assert results[0].sample_rows[0]["issue_code"] == "STD_VIEW_COLUMN_ORDER_MISMATCH"
    assert results[0].sample_rows[0]["view_column_name"] == "topic"
    assert results[0].sample_rows[0]["table_column_name"] == "start_ts"
    assert "Call_H" in adapter.column_contract_sql[0]


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


def test_run_view_contract_validations_reports_std_table_missing_locking_view():
    adapter = StubAdapter(
        view_rows=[],
        std_view_rows=[],
        bus_view_rows=[],
        locking_view_rows=[],
        table_view_coverage_rows=[
            {
                "table_database_name": "CallCentre_DOM_STD_T",
                "table_name": "Call_H",
                "expected_view_database_name": "CallCentre_DOM_STD_V",
                "expected_view_name": "Call_H",
                "issue_code": "MISSING_STANDARD_LOCKING_VIEW",
                "repair_hint": (
                    "Create a same-named %_STD_V access view with LOCKING ROW FOR ACCESS."
                ),
            }
        ],
    )

    results = adapter.run_coverage_only()

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "MISSING_STANDARD_LOCKING_VIEW"
    assert results[0].sample_rows[0]["expected_view_database_name"] == "CallCentre_DOM_STD_V"
    assert results[0].sample_rows[0]["expected_view_name"] == "Call_H"


def test_run_view_contract_validations_reports_direct_table_view_without_locking():
    adapter = StubAdapter(
        view_rows=[],
        std_view_rows=[],
        bus_view_rows=[],
        locking_view_rows=[
            {
                "database_name": "CallCentre_DOM_BUS_V",
                "view_name": "Call_Enriched",
                "view_text": (
                    "CREATE VIEW CallCentre_DOM_BUS_V.Call_Enriched AS "
                    "SELECT call_id FROM CallCentre_DOM_STD_T.Call_H;"
                ),
            }
        ],
    )

    results = adapter.run_locking_only()

    assert len(results) == 1
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "DIRECT_TABLE_VIEW_MISSING_LOCK"
    assert results[0].sample_rows[0]["referenced_table"] == "CALLCENTRE_DOM_STD_T.CALL_H"


def test_run_view_contract_validations_accepts_direct_table_view_with_locking():
    adapter = StubAdapter(
        view_rows=[],
        std_view_rows=[],
        bus_view_rows=[],
        locking_view_rows=[
            {
                "database_name": "CallCentre_DOM_STD_V",
                "view_name": "Call_H",
                "view_text": (
                    "CREATE VIEW CallCentre_DOM_STD_V.Call_H AS "
                    "LOCKING ROW FOR ACCESS "
                    "SELECT call_id FROM CallCentre_DOM_STD_T.Call_H;"
                ),
            },
            {
                "database_name": "CallCentre_DOM_BUS_V",
                "view_name": "Call_Enriched",
                "view_text": (
                    "CREATE VIEW CallCentre_DOM_BUS_V.Call_Enriched AS "
                    "LOCKING TABLE CallCentre_DOM_STD_T.Call_H FOR ACCESS "
                    "SELECT call_id FROM CallCentre_DOM_STD_T.Call_H;"
                ),
            },
        ],
    )

    results = adapter.run_locking_only()

    assert [result.status.value for result in results] == ["PASSED", "PASSED"]


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
        locking_view_rows=[],
        explain_error=RuntimeError("Column overall_quality_score not found in db.table"),
    )

    results = run_view_contract_validations("CallCentre", adapter)

    assert len(results) == 2
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "MISSING_COLUMN"
    assert results[0].sample_rows[0]["missing_column"] == "overall_quality_score"
    assert results[1].status.value == "PASSED"
    assert results[1].test_case.test_id == "CALLCENTRE-STD-TABLE-VIEW-COVERAGE"


def test_run_view_contract_validations_reports_missing_view_inventory():
    results = run_view_contract_validations(
        "CallCentre",
        StubAdapter(view_rows=[], std_view_rows=[], bus_view_rows=[], locking_view_rows=[]),
    )

    assert len(results) == 2
    assert results[0].status.value == "FAILED"
    assert results[0].sample_rows[0]["issue_code"] == "NO_PRODUCT_VIEWS_FOUND"
    assert results[1].status.value == "PASSED"
    assert results[1].test_case.test_id == "CALLCENTRE-STD-TABLE-VIEW-COVERAGE"


class StubAdapter:
    def __init__(
        self,
        view_rows,
        std_view_rows=None,
        bus_view_rows=None,
        column_contract_rows=None,
        locking_view_rows=None,
        table_view_coverage_rows=None,
        explain_error=None,
    ):
        self.view_rows = view_rows
        self.std_view_rows = std_view_rows or []
        self.bus_view_rows = bus_view_rows or []
        self.locking_view_rows = locking_view_rows or []
        self.table_view_coverage_rows = table_view_coverage_rows or []
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

    def run_locking_only(self):
        from ai_native_data_product_trust_engine.view_contracts import (
            _run_view_table_locking_validations,
        )

        return _run_view_table_locking_validations("CallCentre", self)

    def run_coverage_only(self):
        from ai_native_data_product_trust_engine.view_contracts import (
            _run_standard_table_view_coverage_validation,
        )

        return [_run_standard_table_view_coverage_validation("CallCentre", self)]

    def run_columns_only(self):
        from ai_native_data_product_trust_engine.view_contracts import (
            _run_standard_view_column_contract_validations,
        )

        return _run_standard_view_column_contract_validations("CallCentre", self)

    def fetch_all(self, sql):
        if sql.startswith("HELP COLUMN"):
            self.help_column_sql.append(sql)
            if self.explain_error:
                raise self.explain_error
            return [{"Column Name": "call_id"}]
        if "FULL OUTER JOIN table_cols" in sql:
            self.column_contract_sql.append(sql)
            return self.column_contract_rows
        if "MISSING_STANDARD_LOCKING_VIEW" in sql:
            return self.table_view_coverage_rows
        if "COALESCE(RequestText" in sql:
            if "_BUS\\_V" in sql:
                return self.bus_view_rows
            if "_STD\\_V" in sql:
                return self.std_view_rows
            if "DatabaseName LIKE 'CallCentre\\_%' ESCAPE '\\'" in sql:
                return self.locking_view_rows
            return self.std_view_rows
        return self.view_rows
