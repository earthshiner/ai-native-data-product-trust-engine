"""Validate deployed view contracts through Teradata-resolved column metadata."""

from __future__ import annotations

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
        )
    ]


def run_view_contract_validations(prefix: str, adapter) -> list[TestResult]:
    view_rows = adapter.fetch_all(_product_views_sql(prefix))
    if not view_rows:
        return [_missing_inventory_result(prefix)]
    return [_run_view_validation(prefix, adapter, row) for row in view_rows]


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
            "Repair the view DDL or dependent object contract, then re-run view contract validation."
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
