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
SQL_OBJECT_PATTERN = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+([A-Za-z][A-Za-z0-9_$#]*\.[A-Za-z][A-Za-z0-9_$#]*)",
    re.I,
)
MISSING_COLUMN_PATTERNS = (
    re.compile(r"Column/Parameter '([^']+)' does not exist", re.I),
    re.compile(r"Column ([A-Za-z0-9_.$]+) not found", re.I),
)
MISSING_OBJECT_PATTERNS = (
    re.compile(r"Object '([^']+)' does not exist", re.I),
    re.compile(r"Table/View '([^']+)' does not exist", re.I),
)
MISSING_FUNCTION_PATTERNS = (
    re.compile(r"Function '([^']+)' does not exist", re.I),
    re.compile(r"Function ([A-Za-z0-9_.$]+) does not exist", re.I),
)
EXHAUSTIVE_RECIPE_PATTERN = re.compile(
    r"\b(batch|bulk|exhaustive|export|extract|full\s+(scan|extract|refresh)|"
    r"offline|training|all\s+rows|complete\s+dataset)\b",
    re.I,
)
BOUNDED_SQL_PATTERNS = (
    re.compile(r"\bTOP\s+\d+\b", re.I),
    re.compile(r"\bSAMPLE\s+\d+\b", re.I),
    re.compile(r"\bFETCH\s+FIRST\s+\d+\s+ROWS?\s+ONLY\b", re.I),
    re.compile(r"\bQUALIFY\b[\s\S]*\bROW_NUMBER\s*\([\s\S]*?\)\s*(?:<=|<)\s*\d+", re.I),
    re.compile(r"\bWHERE\b[\s\S]*\bBETWEEN\b[\s\S]*:", re.I),
    re.compile(r"\bWHERE\b[\s\S]*(?:=|>=|<=|>|<|IN\s*\()[\s\S]*:", re.I),
)
EXPLAIN_FINDING_PATTERNS = (
    (
        "EXPLAIN_MISSING_STATS",
        re.compile(r"\b(?:missing|stale)\s+(?:statistics|stats)\b", re.I),
        "Collect or refresh optimiser statistics for the referenced objects or join columns.",
    ),
    (
        "EXPLAIN_PRODUCT_JOIN",
        re.compile(r"\bproduct\s+join\b", re.I),
        "Review join predicates and relationship metadata to avoid product joins.",
    ),
    (
        "EXPLAIN_ALL_AMP_SCAN",
        re.compile(r"\ball[\s-]*AMPs?\b|\ball\s+AMP\b", re.I),
        "Add selective predicates, use a keyed access path, or confirm the scan is intentional.",
    ),
    (
        "EXPLAIN_DUPLICATED_LARGE_TABLE",
        re.compile(r"\bduplicat(?:e|ed|ing)\b[\s\S]{0,120}\b(?:all[\s-]*AMPs?|large)\b", re.I),
        "Review join order, statistics and table size before publishing this recipe for agents.",
    ),
    (
        "EXPLAIN_LOW_CONFIDENCE",
        re.compile(r"\blow\s+confidence\b|\bno\s+confidence\b", re.I),
        "Improve statistics or simplify predicates so the optimiser has reliable estimates.",
    ),
)


@dataclass(frozen=True)
class BoundSqlTemplate:
    sql: str
    parameters: tuple[str, ...]


def run_query_template_validations(prefix: str, adapter) -> list[TestResult]:
    recipe_rows = adapter.fetch_all(_active_recipes_sql(prefix))
    results: list[TestResult] = []
    for row in recipe_rows:
        results.extend(_run_recipe_validations(prefix, adapter, row))
    return results


def bind_sql_template(sql_template: str) -> BoundSqlTemplate:
    parameters = tuple(dict.fromkeys(PARAMETER_PATTERN.findall(sql_template)))

    def replace(match: re.Match[str]) -> str:
        return _literal_for_parameter(match.group(1))

    return BoundSqlTemplate(
        sql=PARAMETER_PATTERN.sub(replace, sql_template).strip().rstrip(";"),
        parameters=parameters,
    )


def classify_sql_error(message: str, sql_template: str = "") -> str:
    lower_message = message.lower()
    if _uses_native_vector_feature(sql_template):
        return "UNSUPPORTED_CAPABILITY"
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


def extract_sql_error_evidence(
    message: str,
    sql_template: str = "",
) -> dict[str, object]:
    issue_code = classify_sql_error(message, sql_template)
    evidence: dict[str, object] = {
        "issue_code": issue_code,
        "repair_hint": _repair_hint(issue_code),
    }
    if issue_code == "MISSING_COLUMN":
        missing_column = _first_pattern_match(MISSING_COLUMN_PATTERNS, message)
        if missing_column:
            evidence["missing_column"] = missing_column
    elif issue_code == "MISSING_OBJECT":
        missing_object = _first_pattern_match(MISSING_OBJECT_PATTERNS, message)
        if missing_object:
            evidence["missing_object"] = missing_object
    elif issue_code in {"UNSUPPORTED_FUNCTION", "UNSUPPORTED_CAPABILITY"}:
        missing_function = _first_pattern_match(MISSING_FUNCTION_PATTERNS, message)
        if missing_function:
            evidence["missing_function"] = missing_function
        if _uses_native_vector_feature(sql_template):
            evidence["capability"] = "NATIVE_VECTOR"
            evidence["unsupported_feature"] = "TD_VECTORDISTANCE"
    elif issue_code == "SQL_SYNTAX_ERROR":
        syntax_fragment = _syntax_fragment(message)
        if syntax_fragment:
            evidence["syntax_fragment"] = syntax_fragment
    return evidence


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
        ),
        TestCase(
            test_id=f"{prefix.upper()}-QUERY-BOUNDS",
            name="Interactive Query_Cookbook recipes are bounded for safe agent use",
            category=TestCategory.PERFORMANCE,
            severity=TestSeverity.CRITICAL,
            sql=_active_recipes_sql(prefix),
            expected_result=(
                "Interactive recipes include a parameterised predicate or row-limiting clause."
            ),
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy=(
                "Add date/key parameters, TOP, SAMPLE, QUALIFY ROW_NUMBER, FETCH FIRST, "
                "or mark the recipe as an intentional batch/exhaustive pattern."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-QUERY-EXPLAIN-PERF",
            name="Query_Cookbook EXPLAIN plans avoid known performance-risk patterns",
            category=TestCategory.PERFORMANCE,
            severity=TestSeverity.WARNING,
            sql=_active_recipes_sql(prefix),
            expected_result="EXPLAIN output contains no product joins, all-AMP scan warnings, duplicated large table access, missing statistics or low-confidence estimates.",
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy=(
                "Review recipe predicates, join paths, indexes and statistics before broad "
                "agent use."
            ),
        ),
    ]


def _run_recipe_validations(prefix: str, adapter, row: dict[str, object]) -> list[TestResult]:
    recipe_id = str(row.get("recipe_id") or "UNKNOWN_RECIPE")
    recipe_title = str(row.get("recipe_title") or "")
    sql_template = str(row.get("sql_template") or "").strip()
    explain_test_case = TestCase(
        test_id=f"{prefix.upper()}-QUERY-EXPLAIN-{recipe_id}",
        name=f"SQL template explains: {recipe_title or recipe_id}",
        category=TestCategory.QUERY,
        severity=TestSeverity.CRITICAL,
        sql=sql_template,
        expected_result="SQL template explains successfully after deterministic parameter binding.",
        expected=ExpectedResult.ZERO_ROWS,
        repair_strategy="Repair recipe SQL, update stale metadata, or quarantine the failed recipe.",
    )
    bounds_test_case = _recipe_bounds_test_case(prefix, recipe_id, recipe_title, sql_template)
    performance_test_case = _recipe_explain_performance_test_case(
        prefix,
        recipe_id,
        recipe_title,
        sql_template,
    )

    if not sql_template:
        return [
            _failed_result(explain_test_case, recipe_id, recipe_title, "MISSING_SQL_TEMPLATE"),
            _failed_result(bounds_test_case, recipe_id, recipe_title, "MISSING_SQL_TEMPLATE"),
        ]

    bound_template = bind_sql_template(sql_template)
    results = [
        _run_recipe_bounds_validation(bounds_test_case, row, bound_template),
    ]
    try:
        explain_rows = adapter.fetch_all(f"EXPLAIN {bound_template.sql}")
    except Exception as exc:  # noqa: BLE001 - backend errors are classified for evidence.
        evidence = extract_sql_error_evidence(str(exc), sql_template)
        evidence["referenced_objects"] = _referenced_sql_objects(sql_template)
        evidence["objects_to_examine"] = _recipe_objects_to_examine(
            recipe_id,
            recipe_title,
            sql_template,
        )
        results.insert(
            0,
            _failed_result(
                explain_test_case,
                recipe_id,
                recipe_title,
                evidence,
                error_message=str(exc),
                parameters=bound_template.parameters,
            ),
        )
        return results

    results.insert(
        0,
        TestResult(
            test_case=explain_test_case,
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
        ),
    )
    results.append(
        _run_recipe_explain_performance_validation(
            performance_test_case,
            recipe_id,
            recipe_title,
            explain_rows,
        )
    )
    return results


def is_interactive_recipe(row: dict[str, object]) -> bool:
    values = (
        row.get("recipe_title"),
        row.get("recipe_description"),
        row.get("use_case"),
        row.get("performance_notes"),
        row.get("complexity"),
    )
    text = " ".join(str(value or "") for value in values)
    return EXHAUSTIVE_RECIPE_PATTERN.search(text) is None


def is_bounded_sql(sql_template: str) -> bool:
    return any(pattern.search(sql_template) for pattern in BOUNDED_SQL_PATTERNS)


def explain_performance_findings(
    explain_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    explain_text = _explain_text(explain_rows)
    findings: list[dict[str, object]] = []
    for issue_code, pattern, repair_hint in EXPLAIN_FINDING_PATTERNS:
        match = pattern.search(explain_text)
        if match:
            findings.append(
                {
                    "issue_code": issue_code,
                    "finding": match.group(0).strip(),
                    "repair_hint": repair_hint,
                }
            )
    return findings


def _failed_result(
    test_case: TestCase,
    recipe_id: str,
    recipe_title: str,
    evidence: str | dict[str, object],
    error_message: str | None = None,
    parameters: tuple[str, ...] = (),
) -> TestResult:
    if isinstance(evidence, str):
        evidence = {"issue_code": evidence}
    sample_row = {
        "recipe_id": recipe_id,
        "recipe_title": recipe_title,
        "parameters": list(parameters),
        **evidence,
    }
    return TestResult(
        test_case=test_case,
        status=TestStatus.FAILED,
        row_count=1,
        sample_rows=[sample_row],
        error_message=error_message,
    )


def _active_recipes_sql(prefix: str) -> str:
    return f"""
SELECT
    recipe_id
   ,recipe_title
   ,recipe_description
   ,use_case
   ,performance_notes
   ,complexity
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


def _recipe_bounds_test_case(
    prefix: str,
    recipe_id: str,
    recipe_title: str,
    sql_template: str,
) -> TestCase:
    return TestCase(
        test_id=f"{prefix.upper()}-QUERY-BOUNDS-{recipe_id}",
        name=f"Interactive recipe is bounded: {recipe_title or recipe_id}",
        category=TestCategory.PERFORMANCE,
        severity=TestSeverity.CRITICAL,
        sql=sql_template,
        expected_result="Interactive recipe includes a parameterised predicate or row limit.",
        expected=ExpectedResult.ZERO_ROWS,
        repair_strategy=(
            "Add date/key parameters, TOP, SAMPLE, QUALIFY ROW_NUMBER, FETCH FIRST, "
            "or mark the recipe as an intentional batch/exhaustive pattern."
        ),
    )


def _recipe_explain_performance_test_case(
    prefix: str,
    recipe_id: str,
    recipe_title: str,
    sql_template: str,
) -> TestCase:
    return TestCase(
        test_id=f"{prefix.upper()}-QUERY-EXPLAIN-PERF-{recipe_id}",
        name=f"EXPLAIN plan avoids performance-risk patterns: {recipe_title or recipe_id}",
        category=TestCategory.PERFORMANCE,
        severity=TestSeverity.WARNING,
        sql=sql_template,
        expected_result=(
            "EXPLAIN output contains no product joins, all-AMP scan warnings, duplicated "
            "large table access, missing statistics or low-confidence estimates."
        ),
        expected=ExpectedResult.ZERO_ROWS,
        repair_strategy=(
            "Review recipe predicates, join paths, indexes and statistics before broad "
            "agent use."
        ),
    )


def _run_recipe_bounds_validation(
    test_case: TestCase,
    row: dict[str, object],
    bound_template: BoundSqlTemplate,
) -> TestResult:
    recipe_id = str(row.get("recipe_id") or "UNKNOWN_RECIPE")
    recipe_title = str(row.get("recipe_title") or "")
    if not is_interactive_recipe(row):
        return TestResult(
            test_case=test_case,
            status=TestStatus.PASSED,
            row_count=0,
            sample_rows=[
                {
                    "recipe_id": recipe_id,
                    "recipe_title": recipe_title,
                    "validation_mode": "BOUNDS",
                    "interactive_recipe": False,
                }
            ],
        )
    if is_bounded_sql(str(row.get("sql_template") or "")):
        return TestResult(
            test_case=test_case,
            status=TestStatus.PASSED,
            row_count=0,
            sample_rows=[
                {
                    "recipe_id": recipe_id,
                    "recipe_title": recipe_title,
                    "parameters": list(bound_template.parameters),
                    "validation_mode": "BOUNDS",
                    "interactive_recipe": True,
                }
            ],
        )
    return _failed_result(
        test_case,
        recipe_id,
        recipe_title,
        {
            "issue_code": "UNBOUNDED_INTERACTIVE_RECIPE",
            "interactive_recipe": True,
            "parameters": list(bound_template.parameters),
            "missing_bound_type": "parameterised predicate or row-limiting clause",
            "referenced_objects": _referenced_sql_objects(str(row.get("sql_template") or "")),
            "objects_to_examine": _recipe_objects_to_examine(
                recipe_id,
                recipe_title,
                str(row.get("sql_template") or ""),
            ),
            "repair_hint": (
                "Add a date/key parameter, TOP, SAMPLE, QUALIFY ROW_NUMBER, FETCH FIRST, "
                "or mark this as an intentional batch/exhaustive recipe."
            ),
        },
        parameters=bound_template.parameters,
    )


def _run_recipe_explain_performance_validation(
    test_case: TestCase,
    recipe_id: str,
    recipe_title: str,
    explain_rows: list[dict[str, object]],
) -> TestResult:
    findings = explain_performance_findings(explain_rows)
    if not findings:
        return TestResult(
            test_case=test_case,
            status=TestStatus.PASSED,
            row_count=0,
            sample_rows=[
                {
                    "recipe_id": recipe_id,
                    "recipe_title": recipe_title,
                    "validation_mode": "EXPLAIN_PERFORMANCE",
                }
            ],
        )
    return TestResult(
        test_case=test_case,
        status=TestStatus.FAILED,
        row_count=len(findings),
        sample_rows=[
            {
                "recipe_id": recipe_id,
                "recipe_title": recipe_title,
                **finding,
            }
            for finding in findings[:10]
        ],
    )


def _explain_text(explain_rows: list[dict[str, object]]) -> str:
    values: list[str] = []
    for row in explain_rows:
        values.extend(str(value) for value in row.values() if value is not None)
    return "\n".join(values)


def _referenced_sql_objects(sql_template: str) -> list[str]:
    return list(dict.fromkeys(SQL_OBJECT_PATTERN.findall(sql_template)))


def _recipe_objects_to_examine(
    recipe_id: str,
    recipe_title: str,
    sql_template: str,
) -> list[str]:
    objects = [f"Query_Cookbook recipe {recipe_id}: {recipe_title or recipe_id}"]
    referenced_objects = _referenced_sql_objects(sql_template)
    if referenced_objects:
        objects.extend(f"SQL object {object_name}" for object_name in referenced_objects)
    else:
        objects.append("Recipe SQL template text")
    return objects


def _first_pattern_match(patterns: tuple[re.Pattern[str], ...], message: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(message)
        if match:
            return match.group(1)
    return None


def _syntax_fragment(message: str) -> str | None:
    match = re.search(r"Syntax error: ([^.]+)", message, re.I)
    if match:
        return match.group(1).strip()
    return None


def _uses_native_vector_feature(sql_template: str) -> bool:
    return bool(re.search(r"\b(TD_VECTORDISTANCE|VECTOR)\b", sql_template, re.I))


def _repair_hint(issue_code: str) -> str:
    hints = {
        "MISSING_COLUMN": "Update the SQL template or refresh the view/column metadata so the referenced column exists.",
        "MISSING_OBJECT": "Update the SQL template to a deployed object, create the missing view, or quarantine the recipe.",
        "UNSUPPORTED_FUNCTION": "Replace the function with a supported implementation or mark the required capability unavailable.",
        "UNSUPPORTED_CAPABILITY": "Use a capability-compatible recipe variant or mark the native capability unavailable.",
        "SQL_SYNTAX_ERROR": "Repair the SQL template syntax before publishing the recipe.",
        "SQL_VALIDATION_ERROR": "Inspect the backend error and add a specific classifier if the pattern is repeatable.",
    }
    return hints.get(issue_code, "Review the SQL template and metadata contract.")
