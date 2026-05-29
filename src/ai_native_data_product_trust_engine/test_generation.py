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
            test_id=f"{prefix.upper()}-SEM-004",
            name="Deployed modules are registered in data_product_map",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
WITH deployed_modules AS
(
    SELECT
        CASE
            WHEN tv.DatabaseName LIKE '{prefix}_DOM\\_%' ESCAPE '\\' THEN 'Domain'
            WHEN tv.DatabaseName LIKE '{prefix}_SEM\\_%' ESCAPE '\\' THEN 'Semantic'
            WHEN tv.DatabaseName LIKE '{prefix}_SCH\\_%' ESCAPE '\\' THEN 'Search'
            WHEN tv.DatabaseName LIKE '{prefix}_PRE\\_%' ESCAPE '\\' THEN 'Prediction'
            WHEN tv.DatabaseName LIKE '{prefix}_MEM\\_%' ESCAPE '\\' THEN 'Memory'
            WHEN tv.DatabaseName LIKE '{prefix}_OBS\\_%' ESCAPE '\\' THEN 'Observability'
            ELSE NULL
        END AS module_name
       ,CASE
            WHEN tv.DatabaseName LIKE '{prefix}_DOM\\_%' ESCAPE '\\' THEN '{prefix}_DOM_%'
            WHEN tv.DatabaseName LIKE '{prefix}_SEM\\_%' ESCAPE '\\' THEN '{prefix}_SEM_%'
            WHEN tv.DatabaseName LIKE '{prefix}_SCH\\_%' ESCAPE '\\' THEN '{prefix}_SCH_%'
            WHEN tv.DatabaseName LIKE '{prefix}_PRE\\_%' ESCAPE '\\' THEN '{prefix}_PRE_%'
            WHEN tv.DatabaseName LIKE '{prefix}_MEM\\_%' ESCAPE '\\' THEN '{prefix}_MEM_%'
            WHEN tv.DatabaseName LIKE '{prefix}_OBS\\_%' ESCAPE '\\' THEN '{prefix}_OBS_%'
            ELSE NULL
        END AS database_pattern
       ,MIN(tv.DatabaseName) AS representative_database_name
       ,COUNT(DISTINCT tv.DatabaseName) AS deployed_database_count
    FROM DBC.TablesV tv
    WHERE tv.DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
      AND tv.TableKind IN ('T', 'V')
    GROUP BY 1, 2
)
SELECT
    dm.module_name
   ,dm.database_pattern
   ,dm.representative_database_name
   ,dm.deployed_database_count
   ,'{prefix}_SEM_STD_V' AS semantic_database_name
   ,'MISSING_DATA_PRODUCT_MAP_MODULE' AS issue_code
   ,'Insert an active data_product_map row for the deployed module.' AS repair_hint
   ,1 AS safe_auto_apply
FROM deployed_modules dm
LEFT OUTER JOIN {sem_db}.data_product_map dpm
    ON COALESCE(dpm.is_active, 1) = 1
   AND (
        UPPER(dpm.module_name) = UPPER(dm.module_name)
        OR dpm.database_name LIKE dm.database_pattern
   )
WHERE dm.module_name IS NOT NULL
  AND dpm.module_name IS NULL;
""".strip(),
            expected_result="Returns zero rows.",
            repair_strategy="Insert a missing active data_product_map row for deterministically inferred deployed modules.",
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
    ]
