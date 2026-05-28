"""Validate Query_Cookbook SQL templates."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
)

PARAMETER_PATTERN = re.compile(r"(?<!:):([A-Za-z][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class BoundSqlTemplate:
    sql: str
    parameters: tuple[str, ...]


def run_query_template_validations(prefix: str, adapter) -> list[TestResult]:
    recipe_rows = adapter.fetch_all(_active_recipes_sql(prefix))
    return [_run_recipe_validation(prefix, adapter, row) for row in recipe_rows]


def bind_sql_template(sql_template: str) -> BoundSqlTemplate:
    parameters = tuple(dict.fromkeys(PARAMETER_PATTERN.findall(sql_template)))

    def replace(match: re.Match[str]) -> str:
        return _literal_for_parameter(match.group(1))

    return BoundSqlTemplate(
        sql=PARAMETER_PATTERN.sub(replace, sql_template).strip().rstrip(";"),
        parameters=parameters,
    )


def classify_sql_error(message: str) -> str:
    lower_message = message.lower()
    if "column" in lower_message and "not found" in lower_message:
        return "MISSING_COLUMN"
    if "column/parameter" in lower_message and "does not exist" in lower_message:
        return "MISSING_COLUMN"
    if "table/view" in lower_message and "does not exist" in lower_message:
        return "MISSING_OBJECT"
    if "object" in lower_message and "does not exist" in lower_message:
        return "MISSING_OBJECT"
    if "function" in lower_message and "does not exist" in lower_message:
        return "UNSUPPORTED_FUNCTION"
    if "syntax error" in lower_message:
        return "SQL_SYNTAX_ERROR"
    return "SQL_VALIDATION_ERROR"


def query_template_test_cases(prefix: str) -> list[TestCase]:
    return [
        TestCase(
            test_id=f"{prefix.upper()}-QUERY-EXPLAIN",
            name="Active Query_Cookbook SQL templates explain successfully",
            category=TestCategory.QUERY,
            severity=TestSeverity.CRITICAL,
            sql=_active_recipes_sql(prefix),
            expected_result="Every active SQL template can be parameter-bound and explained.",
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy="Repair recipe SQL, update stale metadata, or quarantine failed recipes.",
        )
    ]


def _run_recipe_validation(prefix: str, adapter, row: dict[str, object]) -> TestResult:
    recipe_id = str(row.get("recipe_id") or "UNKNOWN_RECIPE")
    recipe_title = str(row.get("recipe_title") or "")
    sql_template = str(row.get("sql_template") or "").strip()
    test_case = TestCase(
        test_id=f"{prefix.upper()}-QUERY-EXPLAIN-{recipe_id}",
        name=f"SQL template explains: {recipe_title or recipe_id}",
        category=TestCategory.QUERY,
        severity=TestSeverity.CRITICAL,
        sql=sql_template,
        expected_result="SQL template explains successfully after deterministic parameter binding.",
        expected=ExpectedResult.ZERO_ROWS,
        repair_strategy="Repair recipe SQL, update stale metadata, or quarantine the failed recipe.",
    )

    if not sql_template:
        return _failed_result(test_case, recipe_id, recipe_title, "MISSING_SQL_TEMPLATE")

    bound_template = bind_sql_template(sql_template)
    try:
        adapter.fetch_all(f"EXPLAIN {bound_template.sql}")
    except Exception as exc:  # noqa: BLE001 - backend errors are classified for evidence.
        issue_code = classify_sql_error(str(exc))
        return _failed_result(
            test_case,
            recipe_id,
            recipe_title,
            issue_code,
            error_message=str(exc),
            parameters=bound_template.parameters,
        )

    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED,
        row_count=0,
        sample_rows=[
            {
                "recipe_id": recipe_id,
                "recipe_title": recipe_title,
                "parameters": list(bound_template.parameters),
                "validation_mode": "EXPLAIN",
            }
        ],
    )


def _failed_result(
    test_case: TestCase,
    recipe_id: str,
    recipe_title: str,
    issue_code: str,
    error_message: str | None = None,
    parameters: tuple[str, ...] = (),
) -> TestResult:
    return TestResult(
        test_case=test_case,
        status=TestStatus.FAILED,
        row_count=1,
        sample_rows=[
            {
                "recipe_id": recipe_id,
                "recipe_title": recipe_title,
                "issue_code": issue_code,
                "parameters": list(parameters),
            }
        ],
        error_message=error_message,
    )


def _active_recipes_sql(prefix: str) -> str:
    return f"""
SELECT
    recipe_id
   ,recipe_title
   ,sql_template
FROM {prefix}_MEM_STD_V.Query_Cookbook
WHERE COALESCE(is_active, 1) = 1
ORDER BY recipe_id
""".strip()


def _literal_for_parameter(parameter_name: str) -> str:
    lowered = parameter_name.lower()
    if lowered in {"top_k", "limit", "row_limit", "sample_size"}:
        return "20"
    if "date" in lowered or lowered.endswith("_dts") or lowered.endswith("_ts"):
        return "DATE '2025-01-01'"
    if "score" in lowered or "threshold" in lowered or "ratio" in lowered:
        return "0.5"
    if lowered.startswith("is_") or lowered.endswith("_flag"):
        return "1"
    if lowered == "topic":
        return "'__ADP_TRUST_TOPIC__'"
    if lowered == "day_of_week":
        return "'Monday'"
    if lowered.endswith("_id") or lowered in {"query_call_id", "call_id"}:
        return "'__ADP_TRUST_SAMPLE_ID__'"
    return "'__ADP_TRUST_VALUE__'"
