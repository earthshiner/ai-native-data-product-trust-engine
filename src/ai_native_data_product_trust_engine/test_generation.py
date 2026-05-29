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
            test_id=f"{prefix.upper()}-STRUCT-001",
            name="Similar column names use consistent datatypes",
            category=TestCategory.STRUCTURAL,
            severity=TestSeverity.WARNING,
            sql=f"""
WITH product_columns AS
(
    SELECT
        UPPER(OREPLACE(TRIM(colv.ColumnName), '_', '')) AS normalised_column_name
       ,TRIM(colv.DatabaseName) AS database_name
       ,TRIM(colv.TableName) AS table_name
       ,TRIM(colv.ColumnName) AS column_name
       ,TRIM(colv.ColumnType) AS column_type
       ,colv.ColumnLength AS column_length
       ,colv.DecimalTotalDigits AS decimal_total_digits
       ,colv.DecimalFractionalDigits AS decimal_fractional_digits
       ,TRIM(colv.ColumnType)
            || ':' || CAST(COALESCE(colv.ColumnLength, 0) AS VARCHAR(20))
            || ':' || CAST(COALESCE(colv.DecimalTotalDigits, 0) AS VARCHAR(20))
            || ':' || CAST(COALESCE(colv.DecimalFractionalDigits, 0) AS VARCHAR(20))
            AS type_signature
    FROM DBC.ColumnsV colv
    INNER JOIN DBC.TablesV tv
        ON tv.DatabaseName = colv.DatabaseName
       AND tv.TableName = colv.TableName
    WHERE colv.DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
      AND tv.TableKind IN ('T', 'V')
),
drifted_names AS
(
    SELECT
        normalised_column_name
    FROM product_columns
    GROUP BY normalised_column_name
    HAVING COUNT(DISTINCT type_signature) > 1
)
SELECT
    pc.normalised_column_name
   ,pc.database_name
   ,pc.table_name
   ,pc.column_name
   ,pc.column_type
   ,pc.column_length
   ,pc.decimal_total_digits
   ,pc.decimal_fractional_digits
   ,pc.type_signature
   ,'COLUMN_TYPE_DRIFT' AS issue_code
   ,'Align datatype, length, precision and scale for same/similar columns.' AS repair_hint
FROM product_columns pc
INNER JOIN drifted_names dn
    ON dn.normalised_column_name = pc.normalised_column_name
ORDER BY pc.normalised_column_name, pc.column_name, pc.database_name, pc.table_name;
""".strip(),
            expected_result="Returns zero rows.",
            repair_strategy=(
                "Review same/similar column names with different physical type signatures. "
                "Align datatypes where they participate in joins, filters or generated SQL."
            ),
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
            test_id=f"{prefix.upper()}-STRUCT-002",
            name="Tables have acceptable AMP storage skew",
            category=TestCategory.STRUCTURAL,
            severity=TestSeverity.WARNING,
            sql=f"""
WITH table_amp_usage AS
(
    SELECT
        TRIM(tsv.DatabaseName) AS database_name
       ,TRIM(tsv.TableName) AS table_name
       ,tsv.Vproc AS amp_id
       ,COALESCE(tsv.CurrentPerm, 0) AS current_perm_bytes
    FROM DBC.TableSizeV tsv
    INNER JOIN DBC.TablesV tv
        ON tv.DatabaseName = tsv.DatabaseName
       AND tv.TableName = tsv.TableName
    WHERE tsv.DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
      AND tv.TableKind = 'T'
),
table_skew AS
(
    SELECT
        database_name
       ,table_name
       ,COUNT(*) AS amp_count
       ,SUM(current_perm_bytes) AS total_perm_bytes
       ,MIN(current_perm_bytes) AS min_amp_perm_bytes
       ,MAX(current_perm_bytes) AS max_amp_perm_bytes
       ,AVG(current_perm_bytes) AS avg_amp_perm_bytes
       ,CASE
            WHEN MAX(current_perm_bytes) = 0 THEN 0
            ELSE 100 - ((AVG(current_perm_bytes) / MAX(current_perm_bytes)) * 100)
        END AS skew_percent
    FROM table_amp_usage
    GROUP BY database_name, table_name
)
SELECT
    database_name
   ,table_name
   ,amp_count
   ,total_perm_bytes
   ,min_amp_perm_bytes
   ,max_amp_perm_bytes
   ,avg_amp_perm_bytes
   ,skew_percent
   ,'TABLE_AMP_SKEW' AS issue_code
   ,'Review primary index choice or data distribution for this table.' AS repair_hint
FROM table_skew
WHERE total_perm_bytes > 0
  AND amp_count > 1
  AND skew_percent > 20
ORDER BY skew_percent DESC, total_perm_bytes DESC, database_name, table_name;
""".strip(),
            expected_result="Returns zero rows for tables above the AMP skew threshold.",
            repair_strategy=(
                "Review the table primary index, data distribution, and collected statistics."
            ),
        ),
    ]
