"""Validate relationship and temporal metadata against bounded data evidence."""

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

SAMPLE_LIMIT = 1000


def relationship_health_test_cases(prefix: str) -> list[TestCase]:
    return [
        TestCase(
            test_id=f"{prefix.upper()}-REL-ORPHANS",
            name="Declared relationships have bounded orphan evidence",
            category=TestCategory.DATA_QUALITY,
            severity=TestSeverity.WARNING,
            sql=_relationship_metadata_sql(prefix),
            expected_result=(
                "Active relationships have no source-to-target or target-to-source orphan "
                "evidence in the bounded sample."
            ),
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy=(
                "Repair key values, deactivate incorrect relationship metadata, or document why "
                "the unmatched side is expected."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-REL-CARDINALITY",
            name="Declared relationship cardinality matches bounded key behaviour",
            category=TestCategory.DATA_QUALITY,
            severity=TestSeverity.WARNING,
            sql=_relationship_metadata_sql(prefix),
            expected_result=(
                "Observed duplicate key behaviour does not contradict declared 1:1, 1:M or M:1 "
                "relationship cardinality in the bounded sample."
            ),
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy=(
                "Correct relationship cardinality metadata, cleanse duplicate keys, or split the "
                "relationship into the correct associative pattern."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-TEMPORAL-CURRENT",
            name="Temporal entities have valid current-record contracts",
            category=TestCategory.DATA_QUALITY,
            severity=TestSeverity.WARNING,
            sql=_temporal_entity_metadata_sql(prefix),
            expected_result=(
                "Temporal entities have at most one current non-deleted row per natural key and "
                "declared current views filter on the current flag."
            ),
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy=(
                "Repair duplicate current rows, refresh temporal metadata, or recreate current "
                "views with the declared current-record filter."
            ),
        ),
    ]


def run_relationship_health_validations(prefix: str, adapter) -> list[TestResult]:
    return [
        *run_relationship_orphan_validations(prefix, adapter),
        *run_relationship_cardinality_validations(prefix, adapter),
        *run_temporal_current_validations(prefix, adapter),
    ]


def run_relationship_orphan_validations(prefix: str, adapter) -> list[TestResult]:
    test_case_template = relationship_health_test_cases(prefix)[0]
    rows = _fetch_metadata(adapter, test_case_template)
    if isinstance(rows, TestResult):
        return [rows]
    return [
        _run_relationship_orphan_validation(prefix, adapter, row)
        for row in rows
    ]


def run_relationship_cardinality_validations(prefix: str, adapter) -> list[TestResult]:
    test_case_template = relationship_health_test_cases(prefix)[1]
    rows = _fetch_metadata(adapter, test_case_template)
    if isinstance(rows, TestResult):
        return [rows]
    return [
        _run_relationship_cardinality_validation(prefix, adapter, row)
        for row in rows
    ]


def run_temporal_current_validations(prefix: str, adapter) -> list[TestResult]:
    test_case_template = relationship_health_test_cases(prefix)[2]
    rows = _fetch_metadata(adapter, test_case_template)
    if isinstance(rows, TestResult):
        return [rows]
    return [_run_temporal_current_validation(prefix, adapter, row) for row in rows]


def _run_relationship_orphan_validation(
    prefix: str,
    adapter,
    row: dict[str, object],
) -> TestResult:
    test_case = _relationship_test_case(
        prefix,
        row,
        "REL-ORPHANS",
        "Relationship orphan evidence",
        TestSeverity.WARNING,
    )
    try:
        findings = adapter.fetch_all(_relationship_orphan_sql(row))
    except Exception as exc:  # noqa: BLE001 - backend errors are reported as validation evidence.
        return _error_result(test_case, exc)

    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED if not findings else TestStatus.FAILED,
        row_count=len(findings),
        sample_rows=findings[:10],
    )


def _run_relationship_cardinality_validation(
    prefix: str,
    adapter,
    row: dict[str, object],
) -> TestResult:
    test_case = _relationship_test_case(
        prefix,
        row,
        "REL-CARDINALITY",
        "Relationship cardinality evidence",
        TestSeverity.WARNING,
    )
    cardinality = _normalise_cardinality(row.get("cardinality"))
    if cardinality == "M:M":
        return TestResult(
            test_case=test_case,
            status=TestStatus.PASSED,
            row_count=0,
            sample_rows=[_relationship_pass_row(row, "CARDINALITY_SAMPLE")],
        )

    try:
        findings = adapter.fetch_all(_relationship_cardinality_sql(row, cardinality))
    except Exception as exc:  # noqa: BLE001 - backend errors are reported as validation evidence.
        return _error_result(test_case, exc)

    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED if not findings else TestStatus.FAILED,
        row_count=len(findings),
        sample_rows=findings[:10],
    )


def _run_temporal_current_validation(
    prefix: str,
    adapter,
    row: dict[str, object],
) -> TestResult:
    entity_name = str(row.get("entity_name") or row.get("table_name") or "UNKNOWN_ENTITY")
    test_case = TestCase(
        test_id=f"{prefix.upper()}-TEMPORAL-CURRENT-{_slug(entity_name)}",
        name=f"Temporal current-record contract: {entity_name}",
        category=TestCategory.DATA_QUALITY,
        severity=TestSeverity.WARNING,
        sql=_temporal_current_duplicate_sql(row),
        expected_result=(
            "Returns zero duplicate current rows and verifies the declared current view filters "
            "on the current flag."
        ),
        expected=ExpectedResult.ZERO_ROWS,
        repair_strategy=(
            "Repair duplicate current rows, correct entity_metadata current flags, or recreate "
            "the current view filter."
        ),
    )

    findings: list[dict[str, object]] = []
    try:
        findings.extend(adapter.fetch_all(test_case.sql))
        findings.extend(_current_view_findings(adapter, row))
    except Exception as exc:  # noqa: BLE001 - backend errors are reported as validation evidence.
        return _error_result(test_case, exc)

    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED if not findings else TestStatus.FAILED,
        row_count=len(findings),
        sample_rows=findings[:10],
    )


def _fetch_metadata(adapter, test_case: TestCase) -> list[dict[str, object]] | TestResult:
    try:
        return adapter.fetch_all(test_case.sql)
    except Exception as exc:  # noqa: BLE001 - adapters normalise backend errors later.
        return _error_result(test_case, exc)


def _relationship_test_case(
    prefix: str,
    row: dict[str, object],
    test_id_part: str,
    name: str,
    severity: TestSeverity,
) -> TestCase:
    relationship_name = str(row.get("relationship_name") or row.get("relationship_id") or "UNKNOWN")
    return TestCase(
        test_id=f"{prefix.upper()}-{test_id_part}-{_slug(relationship_name)}",
        name=f"{name}: {relationship_name}",
        category=TestCategory.DATA_QUALITY,
        severity=severity,
        sql="Dynamic bounded relationship evidence query.",
        expected_result="Returns zero rows.",
        expected=ExpectedResult.ZERO_ROWS,
        repair_strategy="Repair metadata, cleanse keys, or document the relationship exception.",
    )


def _relationship_orphan_sql(row: dict[str, object]) -> str:
    source_db, source_table, source_col = _relationship_source(row)
    target_db, target_table, target_col = _relationship_target(row)
    relationship_name = _sql_string(row.get("relationship_name"))
    return f"""
WITH source_sample AS
(
    SELECT TOP {SAMPLE_LIMIT}
        {_qi(source_col)} AS join_value
    FROM {_qname(source_db, source_table)}
    WHERE {_qi(source_col)} IS NOT NULL
),
target_sample AS
(
    SELECT TOP {SAMPLE_LIMIT}
        {_qi(target_col)} AS join_value
    FROM {_qname(target_db, target_table)}
    WHERE {_qi(target_col)} IS NOT NULL
),
source_to_target AS
(
    SELECT
        COUNT(*) AS sample_count
       ,SUM(CASE WHEN tgt.join_value IS NULL THEN 1 ELSE 0 END) AS orphan_count
    FROM source_sample src
    LEFT OUTER JOIN (SELECT DISTINCT join_value FROM target_sample) tgt
        ON tgt.join_value = src.join_value
),
target_to_source AS
(
    SELECT
        COUNT(*) AS sample_count
       ,SUM(CASE WHEN src.join_value IS NULL THEN 1 ELSE 0 END) AS orphan_count
    FROM target_sample tgt
    LEFT OUTER JOIN (SELECT DISTINCT join_value FROM source_sample) src
        ON src.join_value = tgt.join_value
)
SELECT
    {relationship_name} AS relationship_name
   ,'SOURCE_TO_TARGET_ORPHAN' AS issue_code
   ,sample_count
   ,orphan_count
   ,CASE WHEN sample_count = 0 THEN 0 ELSE (orphan_count * 100.00) / sample_count END
        AS orphan_rate_percent
   ,'source' AS affected_side
   ,'Repair source keys, target keys, or deactivate incorrect relationship metadata.' AS repair_hint
FROM source_to_target
WHERE orphan_count > 0
UNION ALL
SELECT
    {relationship_name}
   ,'TARGET_TO_SOURCE_ORPHAN'
   ,sample_count
   ,orphan_count
   ,CASE WHEN sample_count = 0 THEN 0 ELSE (orphan_count * 100.00) / sample_count END
   ,'target'
   ,'Review whether unreferenced target keys are expected; otherwise cleanse keys or relationship metadata.'
FROM target_to_source
WHERE orphan_count > 0
""".strip()


def _relationship_cardinality_sql(row: dict[str, object], cardinality: str) -> str:
    source_db, source_table, source_col = _relationship_source(row)
    target_db, target_table, target_col = _relationship_target(row)
    relationship_name = _sql_string(row.get("relationship_name"))
    source_unique_required = "1" if cardinality in {"1:1", "1:M"} else "0"
    target_unique_required = "1" if cardinality in {"1:1", "M:1"} else "0"
    return f"""
WITH source_sample AS
(
    SELECT TOP {SAMPLE_LIMIT}
        {_qi(source_col)} AS join_value
    FROM {_qname(source_db, source_table)}
    WHERE {_qi(source_col)} IS NOT NULL
),
target_sample AS
(
    SELECT TOP {SAMPLE_LIMIT}
        {_qi(target_col)} AS join_value
    FROM {_qname(target_db, target_table)}
    WHERE {_qi(target_col)} IS NOT NULL
),
source_duplicates AS
(
    SELECT
        join_value
       ,COUNT(*) AS duplicate_count
    FROM source_sample
    GROUP BY join_value
    HAVING COUNT(*) > 1
),
target_duplicates AS
(
    SELECT
        join_value
       ,COUNT(*) AS duplicate_count
    FROM target_sample
    GROUP BY join_value
    HAVING COUNT(*) > 1
)
SELECT
    {relationship_name} AS relationship_name
   ,'CARDINALITY_SOURCE_NOT_UNIQUE' AS issue_code
   ,'{_sql_string_literal(cardinality)}' AS declared_cardinality
   ,'source' AS affected_side
   ,COUNT(*) AS duplicate_key_count
   ,MAX(duplicate_count) AS max_duplicate_count
   ,'Declared cardinality expects source-side uniqueness; cleanse duplicate keys or correct cardinality metadata.' AS repair_hint
FROM source_duplicates
WHERE {source_unique_required} = 1
HAVING COUNT(*) > 0
UNION ALL
SELECT
    {relationship_name}
   ,'CARDINALITY_TARGET_NOT_UNIQUE'
   ,'{_sql_string_literal(cardinality)}'
   ,'target'
   ,COUNT(*)
   ,MAX(duplicate_count)
   ,'Declared cardinality expects target-side uniqueness; cleanse duplicate keys or correct cardinality metadata.'
FROM target_duplicates
WHERE {target_unique_required} = 1
HAVING COUNT(*) > 0
""".strip()


def _temporal_current_duplicate_sql(row: dict[str, object]) -> str:
    database_name = _required_text(row, "database_name")
    table_name = _required_text(row, "table_name")
    natural_key_column = _required_text(row, "natural_key_column")
    current_flag_column = _required_text(row, "current_flag_column")
    deleted_flag_column = str(row.get("deleted_flag_column") or "").strip()
    deleted_filter = ""
    if deleted_flag_column:
        deleted_filter = f"\n      AND COALESCE({_qi(deleted_flag_column)}, 0) = 0"
    entity_name = _sql_string(row.get("entity_name") or table_name)
    # Aggregate over the FULL table — never a sample. Duplicate-current rows are sparse
    # (a handful of keys among millions), so a TOP-N sample of the input misses them
    # almost every time. The GROUP BY/HAVING is a single bounded pass; only the evidence
    # list of offending keys is capped, by TOP on the result of the aggregation.
    return f"""
WITH duplicate_current AS
(
    SELECT
        {_qi(natural_key_column)} AS natural_key
       ,COUNT(*) AS current_row_count
    FROM {_qname(database_name, table_name)}
    WHERE {_qi(current_flag_column)} = 1{deleted_filter}
    GROUP BY {_qi(natural_key_column)}
    HAVING COUNT(*) > 1
)
SELECT TOP {SAMPLE_LIMIT}
    {entity_name} AS entity_name
   ,'DUPLICATE_CURRENT_RECORD' AS issue_code
   ,natural_key
   ,current_row_count
   ,'Repair temporal current flags so each natural key has at most one current non-deleted row.' AS repair_hint
FROM duplicate_current
ORDER BY current_row_count DESC, natural_key
""".strip()


def _current_view_findings(adapter, row: dict[str, object]) -> list[dict[str, object]]:
    view_name = str(row.get("view_name") or "").strip()
    current_flag_column = str(row.get("current_flag_column") or "").strip()
    if not view_name:
        return [
            {
                "entity_name": row.get("entity_name"),
                "issue_code": "CURRENT_VIEW_NOT_DECLARED",
                "repair_hint": "Populate entity_metadata.view_name for temporal current access.",
            }
        ]
    view_rows = adapter.fetch_all(_view_text_sql(_required_text(row, "database_name"), view_name))
    if not view_rows:
        return [
            {
                "entity_name": row.get("entity_name"),
                "view_name": view_name,
                "issue_code": "CURRENT_VIEW_NOT_DEPLOYED",
                "repair_hint": "Deploy the declared current-state view or update entity_metadata.view_name.",
            }
        ]
    view_text = str(view_rows[0].get("view_text") or view_rows[0].get("RequestText") or "")
    if not _view_filters_current(view_text, current_flag_column):
        return [
            {
                "entity_name": row.get("entity_name"),
                "view_name": view_name,
                "current_flag_column": current_flag_column,
                "issue_code": "CURRENT_VIEW_MISSING_CURRENT_FILTER",
                "repair_hint": "Recreate the current-state view with the declared current flag filter.",
            }
        ]
    return []


def _view_filters_current(view_text: str, current_flag_column: str) -> bool:
    normalised = re.sub(r"\s+", " ", view_text).upper()
    column = re.escape(current_flag_column.upper())
    return re.search(rf"\b{column}\b\s*=\s*1\b", normalised) is not None


def _relationship_pass_row(row: dict[str, object], validation_mode: str) -> dict[str, object]:
    return {
        "relationship_name": row.get("relationship_name"),
        "cardinality": row.get("cardinality"),
        "validation_mode": validation_mode,
    }


def _relationship_metadata_sql(prefix: str) -> str:
    sem_db = f"{prefix}_SEM_STD_V"
    return f"""
SELECT
    relationship_id
   ,relationship_name
   ,source_database
   ,source_table
   ,source_column
   ,target_database
   ,target_table
   ,target_column
   ,cardinality
   ,is_mandatory
FROM {sem_db}.table_relationship
WHERE COALESCE(is_active, 1) = 1
  AND source_database IS NOT NULL
  AND target_database IS NOT NULL
  AND {_deployed_module_database_filter(sem_db, 'source_database')}
  AND {_deployed_module_database_filter(sem_db, 'target_database')}
ORDER BY relationship_id
""".strip()


def _temporal_entity_metadata_sql(prefix: str) -> str:
    sem_db = f"{prefix}_SEM_STD_V"
    return f"""
SELECT
    entity_metadata_id
   ,entity_name
   ,database_name
   ,table_name
   ,view_name
   ,natural_key_column
   ,current_flag_column
   ,deleted_flag_column
   ,temporal_pattern
FROM {sem_db}.entity_metadata
WHERE COALESCE(is_active, 1) = 1
  AND database_name IS NOT NULL
  AND table_name IS NOT NULL
  AND {_deployed_module_database_filter(sem_db, 'database_name')}
  AND natural_key_column IS NOT NULL
  AND current_flag_column IS NOT NULL
  AND COALESCE(UPPER(TRIM(temporal_pattern)), 'NONE') <> 'NONE'
ORDER BY entity_metadata_id
""".strip()


def _deployed_module_database_filter(sem_db: str, database_expression: str) -> str:
    return f"""
EXISTS (
    SELECT 1
    FROM {sem_db}.data_product_map module_scope
    WHERE COALESCE(module_scope.is_active, 1) = 1
      AND UPPER(COALESCE(TRIM(module_scope.deployment_status), 'DEPLOYED')) = 'DEPLOYED'
      AND (
          UPPER(TRIM(module_scope.database_name)) = UPPER(TRIM({database_expression}))
          OR UPPER(OREPLACE(OREPLACE(TRIM(module_scope.database_name), '_STD_T', '_STD_V'), '_BUS_V', '_STD_V'))
                = UPPER(TRIM({database_expression}))
          OR UPPER(OREPLACE(OREPLACE(TRIM(module_scope.database_name), '_STD_T', '_BUS_V'), '_STD_V', '_BUS_V'))
                = UPPER(TRIM({database_expression}))
      )
)""".strip()


def _view_text_sql(database_name: str, view_name: str) -> str:
    return f"""
SELECT
    TRIM(DatabaseName) AS database_name
   ,TRIM(TableName) AS view_name
   ,COALESCE(RequestText, '') AS view_text
FROM DBC.TablesV
WHERE DatabaseName = {_sql_string(database_name)}
  AND TableName = {_sql_string(view_name)}
  AND TableKind = 'V'
""".strip()


def _relationship_source(row: dict[str, object]) -> tuple[str, str, str]:
    return (
        _required_text(row, "source_database"),
        _required_text(row, "source_table"),
        _required_text(row, "source_column"),
    )


def _relationship_target(row: dict[str, object]) -> tuple[str, str, str]:
    return (
        _required_text(row, "target_database"),
        _required_text(row, "target_table"),
        _required_text(row, "target_column"),
    )


def _normalise_cardinality(value: object) -> str:
    cardinality = str(value or "").strip().upper().replace(" ", "")
    return cardinality or "UNKNOWN"


def _error_result(test_case: TestCase, exc: Exception) -> TestResult:
    return TestResult(
        test_case=test_case,
        status=TestStatus.ERROR,
        row_count=0,
        error_message=str(exc),
    )


def _required_text(row: dict[str, object], key: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        msg = f"[ADPTrust.MissingRelationshipMetadata] Missing {key}."
        raise ValueError(msg)
    return value


def _qname(database_name: str, object_name: str) -> str:
    return f"{_qi(database_name)}.{_qi(object_name)}"


def _qi(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_string(value: object) -> str:
    return f"'{_sql_string_literal(value)}'"


def _sql_string_literal(value: object) -> str:
    return str(value or "").replace("'", "''")


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-")
    return slug[:80] or "UNKNOWN"
