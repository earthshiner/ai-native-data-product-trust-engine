"""Generate deterministic trust tests from a data product prefix."""

from __future__ import annotations

from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    TestCase,
    TestCategory,
    TestSeverity,
)


def semantic_database(prefix: str) -> str:
    return f"{prefix}_SEM_STD_V"


def memory_database(prefix: str) -> str:
    return f"{prefix}_MEM_STD_V"


def generate_metadata_tests(prefix: str) -> list[TestCase]:
    sem_db = semantic_database(prefix)
    mem_db = memory_database(prefix)

    return [
        TestCase(
            test_id=f"{prefix.upper()}-SEM-001",
            name="Entity metadata references deployed objects",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
SELECT
    em.database_name
   ,em.table_name
FROM {sem_db}.entity_metadata em
LEFT OUTER JOIN DBC.TablesV tv
    ON tv.DatabaseName = em.database_name
   AND tv.TableName = em.table_name
WHERE tv.TableName IS NULL
  AND COALESCE(em.is_active, 1) = 1;
""".strip(),
            expected_result="Returns zero rows.",
            repair_strategy="Mark stale entity metadata inactive or update database_name/table_name to the deployed object.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-002",
            name="Column metadata references deployed columns",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
SELECT
    cmeta.database_name
   ,cmeta.table_name
   ,cmeta.column_name
FROM {sem_db}.column_metadata cmeta
LEFT OUTER JOIN DBC.ColumnsV colv
    ON colv.DatabaseName = cmeta.database_name
   AND colv.TableName = cmeta.table_name
   AND colv.ColumnName = cmeta.column_name
WHERE colv.ColumnName IS NULL
  AND COALESCE(cmeta.is_active, 1) = 1;
""".strip(),
            expected_result="Returns zero rows.",
            repair_strategy="Refresh column metadata from DBC.ColumnsV or deactivate obsolete metadata rows.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-003",
            name="Relationship metadata references deployed join columns",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
SELECT
    tr.relationship_name
   ,tr.source_database
   ,tr.source_table
   ,tr.source_column
   ,tr.target_database
   ,tr.target_table
   ,tr.target_column
FROM {sem_db}.table_relationship tr
LEFT OUTER JOIN DBC.ColumnsV src
    ON src.DatabaseName = tr.source_database
   AND src.TableName = tr.source_table
   AND src.ColumnName = tr.source_column
LEFT OUTER JOIN DBC.ColumnsV tgt
    ON tgt.DatabaseName = tr.target_database
   AND tgt.TableName = tr.target_table
   AND tgt.ColumnName = tr.target_column
WHERE COALESCE(tr.is_active, 1) = 1
  AND (src.ColumnName IS NULL OR tgt.ColumnName IS NULL);
""".strip(),
            expected_result="Returns zero rows.",
            repair_strategy="Repair or deactivate invalid relationship rows before generating joins.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-QUERY-001",
            name="Active cookbook recipes exist and are ready for SQL validation",
            category=TestCategory.QUERY,
            severity=TestSeverity.WARNING,
            sql=f"""
SELECT
    recipe_id
   ,recipe_title
   ,sql_template
FROM {mem_db}.Query_Cookbook
WHERE COALESCE(is_active, 1) = 1;
""".strip(),
            expected_result="Returns active recipes; each recipe is later parameter-bound and explained.",
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy="Flag recipes without SQL templates as metadata defects.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-PERF-001",
            name="Relationship join columns have valid optimiser statistics",
            category=TestCategory.PERFORMANCE,
            severity=TestSeverity.WARNING,
            sql=f"""
WITH required_stats AS
(
    SELECT DISTINCT
        tr.source_database AS database_name
       ,tr.source_table AS table_name
       ,tr.source_column AS column_name
       ,tr.relationship_name
       ,'RELATIONSHIP_SOURCE_JOIN' AS usage_type
    FROM {sem_db}.table_relationship tr
    WHERE COALESCE(tr.is_active, 1) = 1
      AND tr.source_database IS NOT NULL
      AND tr.source_table IS NOT NULL
      AND tr.source_column IS NOT NULL
    UNION
    SELECT DISTINCT
        tr.target_database AS database_name
       ,tr.target_table AS table_name
       ,tr.target_column AS column_name
       ,tr.relationship_name
       ,'RELATIONSHIP_TARGET_JOIN' AS usage_type
    FROM {sem_db}.table_relationship tr
    WHERE COALESCE(tr.is_active, 1) = 1
      AND tr.target_database IS NOT NULL
      AND tr.target_table IS NOT NULL
      AND tr.target_column IS NOT NULL
),
deployed_columns AS
(
    SELECT
        rs.database_name
       ,rs.table_name
       ,rs.column_name
       ,rs.relationship_name
       ,rs.usage_type
    FROM required_stats rs
    INNER JOIN DBC.ColumnsV colv
        ON colv.DatabaseName = rs.database_name
       AND colv.TableName = rs.table_name
       AND colv.ColumnName = rs.column_name
),
valid_column_stats AS
(
    SELECT
        statv.DatabaseName AS database_name
       ,statv.TableName AS table_name
       ,statv.ColumnName AS stats_columns
       ,statv.LastCollectTimeStamp AS last_collect_timestamp
    FROM DBC.ColumnStatsV statv
    WHERE statv.ValidStats = 'Y'
)
SELECT
    dc.database_name
   ,dc.table_name
   ,dc.column_name
   ,dc.relationship_name
   ,dc.usage_type
   ,'MISSING_JOIN_COLUMN_STATS' AS issue_code
   ,'COLLECT STATISTICS COLUMN (' || dc.column_name || ') ON '
        || dc.database_name || '.' || dc.table_name || ';' AS repair_hint
FROM deployed_columns dc
LEFT OUTER JOIN valid_column_stats vcs
    ON vcs.database_name = dc.database_name
   AND vcs.table_name = dc.table_name
   AND POSITION(
        ',' || UPPER(TRIM(dc.column_name)) || ','
        IN ',' || UPPER(
            OREPLACE(OREPLACE(OREPLACE(vcs.stats_columns, ' ', ''), '(', ''), ')', '')
        ) || ','
   ) > 0
WHERE vcs.stats_columns IS NULL
ORDER BY dc.database_name, dc.table_name, dc.column_name, dc.relationship_name;
""".strip(),
            expected_result="Returns zero rows for relationship join columns without valid statistics.",
            repair_strategy=(
                "Collect or refresh optimiser statistics on relationship join columns."
            ),
        ),
    ]
