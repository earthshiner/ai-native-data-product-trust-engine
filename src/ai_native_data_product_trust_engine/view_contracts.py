"""Validate deployed view contracts through Teradata-resolved metadata."""

from __future__ import annotations

import re

from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
)
from ai_native_data_product_trust_engine.query_templates import extract_sql_error_evidence


def view_contract_test_cases(prefix: str) -> list[TestCase]:
    return [
        TestCase(
            test_id=f"{prefix.upper()}-VIEW-COLUMNS",
            name="Deployed data product view columns resolve successfully",
            category=TestCategory.STRUCTURAL,
            severity=TestSeverity.CRITICAL,
            sql=_product_views_sql(prefix),
            expected_result="Every deployed data product view resolves through HELP COLUMN.",
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy=(
                "Repair the view definition, refresh dependent source objects, or update stale "
                "view metadata before publishing the data product."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-STD-VIEW-1TO1",
            name="Standard views are thin 1:1 table contracts",
            category=TestCategory.STRUCTURAL,
            severity=TestSeverity.CRITICAL,
            sql=_standard_view_inventory_sql(prefix),
            expected_result=(
                "Every %_STD_V view is a LOCKING ROW FOR ACCESS 1:1 projection over its "
                "matching %_STD_T table."
            ),
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy=(
                "Move predicates and transformations to the business view layer. Keep %_STD_V "
                "views as explicit column-list 1:1 projections over the matching table."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-STD-TABLE-VIEW-COVERAGE",
            name="Standard tables have matching locking views",
            category=TestCategory.STRUCTURAL,
            severity=TestSeverity.CRITICAL,
            sql=_standard_table_view_coverage_sql(prefix),
            expected_result=(
                "Every %_STD_T table has a same-named %_STD_V access view for governed "
                "agent and application access."
            ),
            expected=ExpectedResult.ZERO_ROWS,
            repair_strategy=(
                "Create the matching %_STD_V view with an explicit column list, LOCKING ROW "
                "FOR ACCESS, and a 1:1 projection over the table."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-BUS-VIEW-SOURCES",
            name="Business views select from standard views",
            category=TestCategory.STRUCTURAL,
            severity=TestSeverity.CRITICAL,
            sql=_business_view_inventory_sql(prefix),
            expected_result=(
                "Every %_BUS_V view selects from %_STD_V views, not directly from tables."
            ),
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy=(
                "Change %_BUS_V views to select from the corresponding %_STD_V access view "
                "instead of selecting directly from %_STD_T tables."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-VIEW-TABLE-LOCKING",
            name="Views that query tables directly use access locks",
            category=TestCategory.STRUCTURAL,
            severity=TestSeverity.CRITICAL,
            sql=_view_text_inventory_sql(prefix),
            expected_result=(
                "Every product view that directly queries a table includes LOCKING ROW FOR "
                "ACCESS or a matching LOCKING TABLE <table> FOR ACCESS modifier."
            ),
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy=(
                "Add LOCKING ROW FOR ACCESS before the SELECT. If LOCKING TABLE is used, "
                "ensure every directly referenced table has the correct table-level lock."
            ),
        ),
    ]


def run_view_contract_validations(prefix: str, adapter) -> list[TestResult]:
    view_rows = adapter.fetch_all(_product_views_sql(prefix))
    if not view_rows:
        return [
            _missing_inventory_result(prefix),
            _run_standard_table_view_coverage_validation(prefix, adapter),
        ]
    results = [_run_view_validation(prefix, adapter, row) for row in view_rows]
    results.append(_run_standard_table_view_coverage_validation(prefix, adapter))
    results.extend(_run_standard_view_contract_validations(prefix, adapter))
    results.extend(_run_business_view_source_validations(prefix, adapter))
    results.extend(_run_view_table_locking_validations(prefix, adapter))
    return results


def _run_standard_view_contract_validations(prefix: str, adapter) -> list[TestResult]:
    view_rows = adapter.fetch_all(_standard_view_inventory_sql(prefix))
    return [_run_standard_view_contract_validation(prefix, adapter, row) for row in view_rows]


def _run_business_view_source_validations(prefix: str, adapter) -> list[TestResult]:
    view_rows = adapter.fetch_all(_business_view_inventory_sql(prefix))
    return [_run_business_view_source_validation(prefix, row) for row in view_rows]


def _run_standard_table_view_coverage_validation(prefix: str, adapter) -> TestResult:
    test_case = view_contract_test_cases(prefix)[2]
    missing_rows = adapter.fetch_all(test_case.sql)
    if missing_rows:
        return TestResult(
            test_case=test_case,
            status=TestStatus.FAILED,
            row_count=len(missing_rows),
            sample_rows=missing_rows[:10],
        )
    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED,
        row_count=0,
        sample_rows=[
            {
                "validation_mode": "STD_TABLE_VIEW_COVERAGE",
                "issue_code": None,
                "repair_hint": "All standard tables have matching standard access views.",
            }
        ],
    )


def _run_view_table_locking_validations(prefix: str, adapter) -> list[TestResult]:
    view_rows = adapter.fetch_all(_view_text_inventory_sql(prefix))
    return [_run_view_table_locking_validation(prefix, row) for row in view_rows]


def _run_view_validation(prefix: str, adapter, row: dict[str, object]) -> TestResult:
    database_name = str(row.get("database_name") or row.get("DatabaseName") or "").strip()
    view_name = str(row.get("view_name") or row.get("TableName") or "").strip()
    qualified_view = _qualified_name(database_name, view_name)
    help_column_sql = _help_column_sql(qualified_view)
    test_case = TestCase(
        test_id=f"{prefix.upper()}-VIEW-COLUMNS-{database_name}.{view_name}",
        name=f"View columns resolve: {database_name}.{view_name}",
        category=TestCategory.STRUCTURAL,
        severity=TestSeverity.CRITICAL,
        sql=help_column_sql,
        expected_result=(
            "Teradata resolves view output columns and all referenced objects, columns, joins "
            "and predicates."
        ),
        repair_strategy=(
            "Repair the view DDL or dependent object contract, then re-run view contract "
            "validation."
        ),
    )

    try:
        column_rows = adapter.fetch_all(test_case.sql)
    except Exception as exc:  # noqa: BLE001 - backend errors are evidence for the contract report.
        evidence = extract_sql_error_evidence(str(exc), test_case.sql)
        return TestResult(
            test_case=test_case,
            status=TestStatus.FAILED,
            row_count=1,
            sample_rows=[
                {
                    "database_name": database_name,
                    "view_name": view_name,
                    "validation_mode": "HELP_COLUMN",
                    **evidence,
                }
            ],
            error_message=str(exc),
        )

    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED,
        row_count=0,
        sample_rows=[
            {
                "database_name": database_name,
                "view_name": view_name,
                "validation_mode": "HELP_COLUMN",
                "resolved_column_count": len(column_rows),
            }
        ],
    )


def _run_standard_view_contract_validation(
    prefix: str,
    adapter,
    row: dict[str, object],
) -> TestResult:
    database_name = str(row.get("database_name") or row.get("DatabaseName") or "").strip()
    view_name = str(row.get("view_name") or row.get("TableName") or "").strip()
    view_text = str(row.get("view_text") or row.get("RequestText") or "")
    base_database_name = database_name.removesuffix("_STD_V") + "_STD_T"
    violations = _standard_view_text_violations(database_name, view_name, view_text)
    violations.extend(
        _standard_view_column_violations(
            adapter,
            database_name,
            view_name,
            base_database_name,
        )
    )
    test_case = TestCase(
        test_id=f"{prefix.upper()}-STD-VIEW-1TO1-{database_name}.{view_name}",
        name=f"Standard view is 1:1: {database_name}.{view_name}",
        category=TestCategory.STRUCTURAL,
        severity=TestSeverity.CRITICAL,
        sql=view_text,
        expected_result=(
            "Standard view has an explicit column list, LOCKING ROW FOR ACCESS, no business "
            "logic, and columns matching the source table ColumnId order."
        ),
        repair_strategy=(
            "Keep the %_STD_V object as a 1:1 access view over the table. Move predicates or "
            "transformations to a %_BUS_V view that selects from the %_STD_V view."
        ),
    )
    if violations:
        return TestResult(
            test_case=test_case,
            status=TestStatus.FAILED,
            row_count=len(violations),
            sample_rows=violations[:10],
        )
    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED,
        row_count=0,
        sample_rows=[
            {
                "database_name": database_name,
                "view_name": view_name,
                "base_database_name": base_database_name,
                "base_table_name": view_name,
                "validation_mode": "STD_VIEW_1TO1",
            }
        ],
    )


def _run_business_view_source_validation(
    prefix: str,
    row: dict[str, object],
) -> TestResult:
    database_name = str(row.get("database_name") or row.get("DatabaseName") or "").strip()
    view_name = str(row.get("view_name") or row.get("TableName") or "").strip()
    view_text = str(row.get("view_text") or row.get("RequestText") or "")
    violations = _business_view_source_violations(database_name, view_name, view_text)
    test_case = TestCase(
        test_id=f"{prefix.upper()}-BUS-VIEW-SOURCES-{database_name}.{view_name}",
        name=f"Business view sources are standard views: {database_name}.{view_name}",
        category=TestCategory.STRUCTURAL,
        severity=TestSeverity.CRITICAL,
        sql=view_text,
        expected_result="Business views select from %_STD_V access views, not %_STD_T tables.",
        repair_strategy=(
            "Change the business view to select from the corresponding %_STD_V view. Keep "
            "business predicates and transformations in %_BUS_V."
        ),
    )
    if violations:
        return TestResult(
            test_case=test_case,
            status=TestStatus.FAILED,
            row_count=len(violations),
            sample_rows=violations[:10],
        )
    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED,
        row_count=0,
        sample_rows=[
            {
                "database_name": database_name,
                "view_name": view_name,
                "validation_mode": "BUS_VIEW_SOURCES",
            }
        ],
    )


def _run_view_table_locking_validation(
    prefix: str,
    row: dict[str, object],
) -> TestResult:
    database_name = str(row.get("database_name") or row.get("DatabaseName") or "").strip()
    view_name = str(row.get("view_name") or row.get("TableName") or "").strip()
    view_text = str(row.get("view_text") or row.get("RequestText") or "")
    violations = _view_table_locking_violations(database_name, view_name, view_text)
    test_case = TestCase(
        test_id=f"{prefix.upper()}-VIEW-TABLE-LOCKING-{database_name}.{view_name}",
        name=f"Direct table view has access locks: {database_name}.{view_name}",
        category=TestCategory.STRUCTURAL,
        severity=TestSeverity.CRITICAL,
        sql=view_text,
        expected_result="Direct table access in views is protected by access locking.",
        repair_strategy=(
            "Prefer LOCKING ROW FOR ACCESS. If using LOCKING TABLE, specify every directly "
            "referenced table exactly."
        ),
    )
    if violations:
        return TestResult(
            test_case=test_case,
            status=TestStatus.FAILED,
            row_count=len(violations),
            sample_rows=violations[:10],
        )
    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED,
        row_count=0,
        sample_rows=[
            {
                "database_name": database_name,
                "view_name": view_name,
                "validation_mode": "VIEW_TABLE_LOCKING",
            }
        ],
    )


def _missing_inventory_result(prefix: str) -> TestResult:
    test_case = view_contract_test_cases(prefix)[0]
    return TestResult(
        test_case=test_case,
        status=TestStatus.FAILED,
        row_count=0,
        sample_rows=[
            {
                "issue_code": "NO_PRODUCT_VIEWS_FOUND",
                "repair_hint": (
                    "Confirm the data product view-layer databases are deployed and visible to "
                    "the validation connection."
                ),
            }
        ],
    )


def _product_views_sql(prefix: str) -> str:
    escaped_prefix = prefix.replace("'", "''")
    return f"""
SELECT
    TRIM(DatabaseName) AS database_name
   ,TRIM(TableName) AS view_name
FROM DBC.TablesV
WHERE DatabaseName LIKE '{escaped_prefix}\\_%' ESCAPE '\\'
  AND TableKind = 'V'
ORDER BY DatabaseName, TableName
""".strip()


def _standard_view_inventory_sql(prefix: str) -> str:
    escaped_prefix = prefix.replace("'", "''")
    return f"""
SELECT
    TRIM(DatabaseName) AS database_name
   ,TRIM(TableName) AS view_name
   ,COALESCE(RequestText, '') AS view_text
FROM DBC.TablesV
WHERE DatabaseName LIKE '{escaped_prefix}\\_%\\_STD\\_V' ESCAPE '\\'
  AND TableKind = 'V'
ORDER BY DatabaseName, TableName
""".strip()


def _business_view_inventory_sql(prefix: str) -> str:
    escaped_prefix = prefix.replace("'", "''")
    return f"""
SELECT
    TRIM(DatabaseName) AS database_name
   ,TRIM(TableName) AS view_name
   ,COALESCE(RequestText, '') AS view_text
FROM DBC.TablesV
WHERE DatabaseName LIKE '{escaped_prefix}\\_%\\_BUS\\_V' ESCAPE '\\'
  AND TableKind = 'V'
ORDER BY DatabaseName, TableName
""".strip()


def _standard_table_view_coverage_sql(prefix: str) -> str:
    escaped_prefix = prefix.replace("'", "''")
    return f"""
WITH standard_tables AS
(
    SELECT
        TRIM(DatabaseName) AS table_database_name
       ,TRIM(TableName) AS table_name
       ,TRIM(SUBSTRING(DatabaseName FROM 1 FOR CHARACTER_LENGTH(DatabaseName) - 6) || '_STD_V')
            AS expected_view_database_name
       ,TRIM(TableName) AS expected_view_name
    FROM DBC.TablesV
    WHERE DatabaseName LIKE '{escaped_prefix}\\_%\\_STD\\_T' ESCAPE '\\'
      AND TableKind = 'T'
),
missing_views AS
(
    SELECT
        st.table_database_name
       ,st.table_name
       ,st.expected_view_database_name
       ,st.expected_view_name
       ,'MISSING_STANDARD_LOCKING_VIEW' AS issue_code
       ,'Create a same-named %_STD_V locking 1:1 access view.' AS repair_hint
    FROM standard_tables st
    LEFT OUTER JOIN DBC.TablesV tv
        ON tv.DatabaseName = st.expected_view_database_name
       AND tv.TableName = st.expected_view_name
       AND tv.TableKind = 'V'
    WHERE tv.TableName IS NULL
)
SELECT
    table_database_name
   ,table_name
   ,expected_view_database_name
   ,expected_view_name
   ,issue_code
   ,repair_hint
FROM missing_views
ORDER BY table_database_name, table_name
""".strip()


def _view_text_inventory_sql(prefix: str) -> str:
    escaped_prefix = prefix.replace("'", "''")
    return f"""
SELECT
    TRIM(DatabaseName) AS database_name
   ,TRIM(TableName) AS view_name
   ,COALESCE(RequestText, '') AS view_text
FROM DBC.TablesV
WHERE DatabaseName LIKE '{escaped_prefix}\\_%' ESCAPE '\\'
  AND TableKind = 'V'
ORDER BY DatabaseName, TableName
""".strip()


def _standard_view_column_contract_sql(
    view_database_name: str,
    view_name: str,
    base_database_name: str,
) -> str:
    return f"""
WITH view_cols AS
(
    SELECT
        ColumnId AS column_id
       ,TRIM(ColumnName) AS column_name
    FROM DBC.ColumnsV
    WHERE DatabaseName = '{_sql_string_value(view_database_name)}'
      AND TableName = '{_sql_string_value(view_name)}'
),
table_cols AS
(
    SELECT
        ColumnId AS column_id
       ,TRIM(ColumnName) AS column_name
    FROM DBC.ColumnsV
    WHERE DatabaseName = '{_sql_string_value(base_database_name)}'
      AND TableName = '{_sql_string_value(view_name)}'
)
SELECT
    COALESCE(vc.column_id, tc.column_id) AS column_id
   ,vc.column_name AS view_column_name
   ,tc.column_name AS table_column_name
FROM view_cols vc
FULL OUTER JOIN table_cols tc
    ON tc.column_id = vc.column_id
WHERE vc.column_name IS NULL
   OR tc.column_name IS NULL
   OR vc.column_name <> tc.column_name
ORDER BY 1
""".strip()


def _standard_view_text_violations(
    database_name: str,
    view_name: str,
    view_text: str,
) -> list[dict[str, object]]:
    normalised = " ".join(view_text.upper().split())
    violations: list[dict[str, object]] = []
    if "LOCKING ROW FOR ACCESS" not in normalised:
        violations.append(_standard_view_violation(database_name, view_name, "MISSING_LOCKING_ROW"))
    if "SELECT *" in normalised:
        violations.append(_standard_view_violation(database_name, view_name, "SELECT_STAR"))
    if not _has_explicit_view_column_list(normalised):
        violations.append(
            _standard_view_violation(database_name, view_name, "MISSING_VIEW_COLUMN_LIST")
        )
    for token in _BUSINESS_LAYER_TOKENS:
        if token in normalised:
            violations.append(
                _standard_view_violation(
                    database_name,
                    view_name,
                    "BUSINESS_LOGIC_IN_STD_VIEW",
                    token,
                )
            )
    return violations


def _standard_view_column_violations(
    adapter,
    database_name: str,
    view_name: str,
    base_database_name: str,
) -> list[dict[str, object]]:
    rows = adapter.fetch_all(
        _standard_view_column_contract_sql(database_name, view_name, base_database_name)
    )
    return [
        {
            "issue_code": "STD_VIEW_COLUMN_ORDER_MISMATCH",
            "repair_hint": (
                "Recreate the %_STD_V view with an explicit column list and SELECT columns in "
                "the same ColumnId order as the matching %_STD_T table."
            ),
            "database_name": database_name,
            "view_name": view_name,
            "base_database_name": base_database_name,
            "base_table_name": view_name,
            "column_id": row.get("column_id"),
            "view_column_name": row.get("view_column_name"),
            "table_column_name": row.get("table_column_name"),
        }
        for row in rows
    ]


def _standard_view_violation(
    database_name: str,
    view_name: str,
    issue_code: str,
    evidence: str | None = None,
) -> dict[str, object]:
    row = {
        "issue_code": issue_code,
        "repair_hint": _standard_view_repair_hint(issue_code),
        "database_name": database_name,
        "view_name": view_name,
    }
    if evidence:
        row["evidence"] = evidence
    return row


def _business_view_source_violations(
    database_name: str,
    view_name: str,
    view_text: str,
) -> list[dict[str, object]]:
    normalised = " ".join(view_text.upper().split())
    if "_STD_T" not in normalised:
        return []
    return [
        {
            "issue_code": "BUS_VIEW_SELECTS_TABLE_DIRECTLY",
            "repair_hint": (
                "Change the %_BUS_V view to select from the corresponding %_STD_V access view "
                "instead of selecting directly from a %_STD_T table."
            ),
            "database_name": database_name,
            "view_name": view_name,
            "evidence": "_STD_T",
        }
    ]


def _view_table_locking_violations(
    database_name: str,
    view_name: str,
    view_text: str,
) -> list[dict[str, object]]:
    normalised = " ".join(view_text.upper().split())
    direct_tables = _direct_table_references(normalised)
    if not direct_tables:
        return []
    if "LOCKING ROW FOR ACCESS" in normalised:
        return []
    return [
        {
            "issue_code": "DIRECT_TABLE_VIEW_MISSING_LOCK",
            "repair_hint": (
                "Add LOCKING ROW FOR ACCESS before the SELECT. LOCKING TABLE is acceptable "
                "only when it names every directly referenced table correctly."
            ),
            "database_name": database_name,
            "view_name": view_name,
            "referenced_table": referenced_table,
        }
        for referenced_table in direct_tables
        if not _has_locking_table_for_reference(normalised, referenced_table)
    ]


def _direct_table_references(normalised_view_text: str) -> list[str]:
    table_names = []
    table_pattern = r"\b(?:FROM|JOIN)\s+([A-Z0-9_]+_T)\.([A-Z0-9_]+)\b"
    for match in re.finditer(table_pattern, normalised_view_text):
        table_names.append(f"{match.group(1)}.{match.group(2)}")
    return list(dict.fromkeys(table_names))


def _has_locking_table_for_reference(normalised_view_text: str, referenced_table: str) -> bool:
    database_name, table_name = referenced_table.split(".", maxsplit=1)
    accepted_locks = (
        f"LOCKING TABLE {database_name}.{table_name} FOR ACCESS",
        f"LOCKING TABLE {table_name} FOR ACCESS",
    )
    return any(lock in normalised_view_text for lock in accepted_locks)


def _standard_view_repair_hint(issue_code: str) -> str:
    hints = {
        "MISSING_LOCKING_ROW": "Add LOCKING ROW FOR ACCESS to the 1:1 access view.",
        "SELECT_STAR": (
            "Replace SELECT * with an explicit column projection in table ColumnId order."
        ),
        "MISSING_VIEW_COLUMN_LIST": "Declare the view column list before AS as the agent contract.",
        "BUSINESS_LOGIC_IN_STD_VIEW": (
            "Move predicates or transformations to a %_BUS_V view that selects from the %_STD_V "
            "view."
        ),
    }
    return hints.get(issue_code, "Recreate the view as a thin 1:1 access view.")


def _qualified_name(database_name: str, object_name: str) -> str:
    return f"{_quote_identifier(database_name)}.{_quote_identifier(object_name)}"


def _help_column_sql(qualified_view: str) -> str:
    return f"""
HELP COLUMN dt.* FROM (
    SELECT *
    FROM {qualified_view}
    WHERE 1 = 0
) AS dt
""".strip()


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_string_value(value: str) -> str:
    return value.replace("'", "''")


def _has_explicit_view_column_list(normalised_view_text: str) -> bool:
    before_as = normalised_view_text.split(" AS ", maxsplit=1)[0]
    return "(" in before_as and ")" in before_as


_BUSINESS_LAYER_TOKENS = (
    " WHERE ",
    " JOIN ",
    " GROUP BY ",
    " HAVING ",
    " QUALIFY ",
    " ORDER BY ",
    " UNION ",
    " INTERSECT ",
    " EXCEPT ",
    " MINUS ",
    " CASE ",
    " CAST(",
    " COALESCE(",
    " OREPLACE(",
    " TRIM(",
    " SUBSTR(",
    " SUM(",
    " COUNT(",
    " AVG(",
    " MIN(",
    " MAX(",
)
