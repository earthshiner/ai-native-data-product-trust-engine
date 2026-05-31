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


def semantic_table_database(prefix: str) -> str:
    return f"{prefix}_SEM_STD_T"


def memory_database(prefix: str) -> str:
    return f"{prefix}_MEM_STD_V"


def generate_metadata_tests(prefix: str) -> list[TestCase]:
    sem_db = semantic_database(prefix)
    sem_table_db = semantic_table_database(prefix)
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
            name="Relationship join columns have compatible datatypes",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
WITH deployed_relationship_columns AS
(
    SELECT
        tr.relationship_name
       ,tr.source_database
       ,tr.source_table
       ,tr.source_column
       ,TRIM(src.ColumnType) AS source_column_type
       ,src.ColumnLength AS source_column_length
       ,src.DecimalTotalDigits AS source_decimal_total_digits
       ,src.DecimalFractionalDigits AS source_decimal_fractional_digits
       ,TRIM(src.CharType) AS source_char_type
       ,tr.target_database
       ,tr.target_table
       ,tr.target_column
       ,TRIM(tgt.ColumnType) AS target_column_type
       ,tgt.ColumnLength AS target_column_length
       ,tgt.DecimalTotalDigits AS target_decimal_total_digits
       ,tgt.DecimalFractionalDigits AS target_decimal_fractional_digits
       ,TRIM(tgt.CharType) AS target_char_type
    FROM {sem_db}.table_relationship tr
    INNER JOIN DBC.ColumnsV src
        ON src.DatabaseName = tr.source_database
       AND src.TableName = tr.source_table
       AND src.ColumnName = tr.source_column
    INNER JOIN DBC.ColumnsV tgt
        ON tgt.DatabaseName = tr.target_database
       AND tgt.TableName = tr.target_table
       AND tgt.ColumnName = tr.target_column
    WHERE COALESCE(tr.is_active, 1) = 1
),
relationship_type_issues AS
(
    SELECT
        relationship_name
       ,source_database
       ,source_table
       ,source_column
       ,source_column_type
       ,source_column_length
       ,source_decimal_total_digits
       ,source_decimal_fractional_digits
       ,source_char_type
       ,target_database
       ,target_table
       ,target_column
       ,target_column_type
       ,target_column_length
       ,target_decimal_total_digits
       ,target_decimal_fractional_digits
       ,target_char_type
       ,CASE
            WHEN source_column_type <> target_column_type
                THEN 'JOIN_COLUMN_TYPE_MISMATCH'
            WHEN COALESCE(source_char_type, '') <> COALESCE(target_char_type, '')
                THEN 'JOIN_COLUMN_CHARSET_MISMATCH'
            WHEN COALESCE(source_decimal_total_digits, -1)
                    <> COALESCE(target_decimal_total_digits, -1)
              OR COALESCE(source_decimal_fractional_digits, -1)
                    <> COALESCE(target_decimal_fractional_digits, -1)
                THEN 'JOIN_COLUMN_PRECISION_SCALE_MISMATCH'
            WHEN COALESCE(source_column_length, -1) <> COALESCE(target_column_length, -1)
                THEN 'JOIN_COLUMN_LENGTH_MISMATCH'
            ELSE NULL
        END AS issue_code
    FROM deployed_relationship_columns
    WHERE source_column_type <> target_column_type
       OR COALESCE(source_char_type, '') <> COALESCE(target_char_type, '')
       OR COALESCE(source_decimal_total_digits, -1)
            <> COALESCE(target_decimal_total_digits, -1)
       OR COALESCE(source_decimal_fractional_digits, -1)
            <> COALESCE(target_decimal_fractional_digits, -1)
       OR COALESCE(source_column_length, -1) <> COALESCE(target_column_length, -1)
)
SELECT
    relationship_name
   ,source_database
   ,source_table
   ,source_column
   ,source_column_type
   ,source_column_length
   ,source_decimal_total_digits
   ,source_decimal_fractional_digits
   ,source_char_type
   ,target_database
   ,target_table
   ,target_column
   ,target_column_type
   ,target_column_length
   ,target_decimal_total_digits
   ,target_decimal_fractional_digits
   ,target_char_type
   ,issue_code
   ,'Align relationship join column datatype, length, precision, scale and character set before generated SQL uses this join.' AS repair_hint
FROM relationship_type_issues
ORDER BY relationship_name, issue_code, source_database, source_table, source_column;
""".strip(),
            expected_result="Returns zero rows for relationship join columns with incompatible type signatures.",
            repair_strategy=(
                "Align the relationship join column datatype, length, precision, scale and "
                "character set, or expose a compatible view-layer join key before agents "
                "generate SQL from this relationship."
            ),
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
            test_id=f"{prefix.upper()}-DISCOVERY-001",
            name="Data product registry table exists",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
SELECT
    '{sem_table_db}' AS database_name
   ,'data_product_registry' AS table_name
   ,'MISSING_DATA_PRODUCT_REGISTRY_TABLE' AS issue_code
   ,'{sem_table_db}.data_product_registry is required for the Data Product Orientation Layer.' AS issue_detail
   ,'Create and comment {sem_table_db}.data_product_registry before publishing MCP product discovery resources.' AS repair_hint
WHERE NOT EXISTS (
    SELECT 1
    FROM DBC.TablesV tv
    WHERE tv.DatabaseName = '{sem_table_db}'
      AND tv.TableName = 'data_product_registry'
      AND tv.TableKind = 'T'
);
""".strip(),
            expected_result=f"Returns zero rows when {sem_table_db}.data_product_registry exists.",
            repair_strategy=(
                f"Create {sem_table_db}.data_product_registry with table and column comments so "
                "the MCP Data Product Orientation Layer has a persistent backing catalogue."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-DISCOVERY-002",
            name="Data product registry matches orientation metadata",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
WITH active_registry AS
(
    SELECT
        product_id
       ,product_name
       ,product_version
       ,semantic_database
       ,memory_database
       ,observability_database
       ,manifest_json
       ,contract_uri
       ,semantic_uri
       ,quality_uri
       ,lineage_uri
       ,policy_uri
       ,approved_entrypoint
       ,approved_access_mode
    FROM {sem_table_db}.data_product_registry
    WHERE COALESCE(is_active, 1) = 1
      AND COALESCE(is_deleted, 0) = 0
      AND semantic_database = '{sem_table_db}'
),
module_map AS
(
    SELECT
        UPPER(TRIM(module_name)) AS module_name
       ,TRIM(database_name) AS database_name
    FROM {sem_db}.data_product_map
    WHERE COALESCE(is_active, 1) = 1
),
registry_issues AS
(
    SELECT
        CAST(NULL AS VARCHAR(128)) AS product_id
       ,'MISSING_PRODUCT_REGISTRY_ROW' AS issue_code
       ,'No active, non-deleted data_product_registry row points to {sem_table_db}.' AS issue_detail
       ,'Insert or refresh {sem_table_db}.data_product_registry for this data product.' AS repair_hint
    WHERE NOT EXISTS (SELECT 1 FROM active_registry)

    UNION ALL

    SELECT
        ar.product_id
       ,'MISSING_ORIENTATION_MANIFEST' AS issue_code
       ,'manifest_json is required for the MCP Data Product Orientation Layer.' AS issue_detail
       ,'Populate manifest_json with the discovery manifest and recommended navigation.' AS repair_hint
    FROM active_registry ar
    WHERE ar.manifest_json IS NULL

    UNION ALL

    SELECT
        ar.product_id
       ,'MISSING_CONTRACT_URI' AS issue_code
       ,'contract_uri is required so clients inspect the product contract before data access.' AS issue_detail
       ,'Populate contract_uri with the contract MCP resource or document URI.' AS repair_hint
    FROM active_registry ar
    WHERE ar.contract_uri IS NULL

    UNION ALL

    SELECT
        ar.product_id
       ,'MISSING_SEMANTIC_URI' AS issue_code
       ,'semantic_uri is required so clients can discover the semantic model from the manifest.' AS issue_detail
       ,'Populate semantic_uri with the semantic MCP resource or metadata URI.' AS repair_hint
    FROM active_registry ar
    WHERE ar.semantic_uri IS NULL

    UNION ALL

    SELECT
        ar.product_id
       ,'MISSING_POLICY_URI' AS issue_code
       ,'policy_uri is required so clients inspect access rules before querying data.' AS issue_detail
       ,'Populate policy_uri with the access-policy MCP resource or policy URI.' AS repair_hint
    FROM active_registry ar
    WHERE ar.policy_uri IS NULL

    UNION ALL

    SELECT
        ar.product_id
       ,'MISSING_APPROVED_ENTRYPOINT' AS issue_code
       ,'approved_entrypoint and approved_access_mode are required for governed data access.' AS issue_detail
       ,'Populate approved_entrypoint and approved_access_mode from the product access layer.' AS repair_hint
    FROM active_registry ar
    WHERE ar.approved_entrypoint IS NULL
       OR ar.approved_access_mode IS NULL

    UNION ALL

    SELECT
        ar.product_id
       ,'SEMANTIC_DATABASE_NOT_IN_MODULE_MAP' AS issue_code
       ,'Registry semantic_database does not match an active Semantic module in data_product_map.' AS issue_detail
       ,'Refresh data_product_registry.semantic_database or Semantic.data_product_map.' AS repair_hint
    FROM active_registry ar
    WHERE NOT EXISTS (
        SELECT 1
        FROM module_map mm
        WHERE mm.module_name = 'SEMANTIC'
          AND mm.database_name = ar.semantic_database
    )

    UNION ALL

    SELECT
        ar.product_id
       ,'MEMORY_DATABASE_NOT_IN_MODULE_MAP' AS issue_code
       ,'Registry memory_database does not match an active Memory module in data_product_map.' AS issue_detail
       ,'Refresh data_product_registry.memory_database or Semantic.data_product_map.' AS repair_hint
    FROM active_registry ar
    WHERE EXISTS (
        SELECT 1
        FROM module_map mm
        WHERE mm.module_name = 'MEMORY'
    )
      AND NOT EXISTS (
        SELECT 1
        FROM module_map mm
        WHERE mm.module_name = 'MEMORY'
          AND mm.database_name = ar.memory_database
    )

    UNION ALL

    SELECT
        ar.product_id
       ,'OBSERVABILITY_DATABASE_NOT_IN_MODULE_MAP' AS issue_code
       ,'Registry observability_database does not match an active Observability module in data_product_map.' AS issue_detail
       ,'Refresh data_product_registry.observability_database or Semantic.data_product_map.' AS repair_hint
    FROM active_registry ar
    WHERE EXISTS (
        SELECT 1
        FROM module_map mm
        WHERE mm.module_name = 'OBSERVABILITY'
    )
      AND NOT EXISTS (
        SELECT 1
        FROM module_map mm
        WHERE mm.module_name = 'OBSERVABILITY'
          AND mm.database_name = ar.observability_database
    )

    UNION ALL

    SELECT
        ar.product_id
       ,'SEMANTIC_DATABASE_NOT_DEPLOYED' AS issue_code
       ,'Registry semantic_database is not visible in DBC.DatabasesV.' AS issue_detail
       ,'Deploy the Semantic database or refresh the registry to the deployed database name.' AS repair_hint
    FROM active_registry ar
    LEFT OUTER JOIN DBC.DatabasesV dbv
        ON dbv.DatabaseName = ar.semantic_database
    WHERE dbv.DatabaseName IS NULL

    UNION ALL

    SELECT
        ar.product_id
       ,'MEMORY_DATABASE_NOT_DEPLOYED' AS issue_code
       ,'Registry memory_database is not visible in DBC.DatabasesV.' AS issue_detail
       ,'Deploy the Memory database or refresh the registry to the deployed database name.' AS repair_hint
    FROM active_registry ar
    LEFT OUTER JOIN DBC.DatabasesV dbv
        ON dbv.DatabaseName = ar.memory_database
    WHERE ar.memory_database IS NOT NULL
      AND dbv.DatabaseName IS NULL
)
SELECT
    product_id
   ,issue_code
   ,issue_detail
   ,repair_hint
FROM registry_issues
ORDER BY issue_code, product_id;
""".strip(),
            expected_result="Returns zero rows when the registry and orientation manifest match deployed metadata.",
            repair_strategy=(
                f"Refresh {sem_table_db}.data_product_registry and its manifest_json so MCP clients "
                "discover the product, contract, semantic model, policy, quality, lineage and "
                "approved access path before querying data."
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
        TestCase(
            test_id=f"{prefix.upper()}-STRUCT-003",
            name="Product tables have healthy primary index definitions",
            category=TestCategory.STRUCTURAL,
            severity=TestSeverity.WARNING,
            sql=f"""
WITH product_tables AS
(
    SELECT
        TRIM(tv.DatabaseName) AS database_name
       ,TRIM(tv.TableName) AS table_name
       ,COALESCE(tv.PIColumnCount, 0) AS pi_column_count
    FROM DBC.TablesV tv
    WHERE tv.DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
      AND tv.TableKind = 'T'
),
table_size AS
(
    SELECT
        TRIM(tsv.DatabaseName) AS database_name
       ,TRIM(tsv.TableName) AS table_name
       ,COUNT(*) AS amp_count
       ,SUM(COALESCE(tsv.CurrentPerm, 0)) AS total_perm_bytes
       ,MIN(COALESCE(tsv.CurrentPerm, 0)) AS min_amp_perm_bytes
       ,MAX(COALESCE(tsv.CurrentPerm, 0)) AS max_amp_perm_bytes
       ,AVG(COALESCE(tsv.CurrentPerm, 0)) AS avg_amp_perm_bytes
       ,CASE
            WHEN MAX(COALESCE(tsv.CurrentPerm, 0)) = 0 THEN 0
            ELSE 100 - (
                (AVG(COALESCE(tsv.CurrentPerm, 0)) / MAX(COALESCE(tsv.CurrentPerm, 0))) * 100
            )
        END AS skew_percent
    FROM DBC.TableSizeV tsv
    GROUP BY TRIM(tsv.DatabaseName), TRIM(tsv.TableName)
),
primary_index_columns AS
(
    SELECT
        TRIM(iv.DatabaseName) AS database_name
       ,TRIM(iv.TableName) AS table_name
       ,LISTAGG(TRIM(iv.ColumnName), ',') WITHIN GROUP (ORDER BY iv.ColumnPosition)
            AS primary_index_columns
       ,COUNT(*) AS primary_index_column_count
       ,SUM(CASE WHEN colv.Nullable = 'Y' THEN 1 ELSE 0 END) AS nullable_pi_column_count
       ,MAX(
            CASE
                WHEN UPPER(TRIM(iv.ColumnName)) LIKE '%STATUS%'
                  OR UPPER(TRIM(iv.ColumnName)) LIKE '%TYPE%'
                  OR UPPER(TRIM(iv.ColumnName)) LIKE '%FLAG%'
                  OR UPPER(TRIM(iv.ColumnName)) LIKE '%GENDER%'
                  OR UPPER(TRIM(iv.ColumnName)) LIKE '%CATEGORY%'
                THEN 1
                ELSE 0
            END
        ) AS suspicious_low_cardinality_name
    FROM DBC.IndicesV iv
    LEFT OUTER JOIN DBC.ColumnsV colv
        ON colv.DatabaseName = iv.DatabaseName
       AND colv.TableName = iv.TableName
       AND colv.ColumnName = iv.ColumnName
    WHERE iv.IndexNumber = 1
      AND iv.IndexType IN ('P', 'Q', 'A', 'K')
      AND iv.DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
    GROUP BY TRIM(iv.DatabaseName), TRIM(iv.TableName)
),
primary_index_issues AS
(
    SELECT
        pt.database_name
       ,pt.table_name
       ,COALESCE(pic.primary_index_columns, '') AS primary_index_columns
       ,COALESCE(ts.total_perm_bytes, 0) AS total_perm_bytes
       ,COALESCE(ts.skew_percent, 0) AS skew_percent
       ,'PRIMARY_INDEX_NOT_DEFINED' AS issue_code
       ,'Define and document an intentional primary index, or explicitly justify NoPI table design.' AS repair_hint
    FROM product_tables pt
    LEFT OUTER JOIN primary_index_columns pic
        ON pic.database_name = pt.database_name
       AND pic.table_name = pt.table_name
    LEFT OUTER JOIN table_size ts
        ON ts.database_name = pt.database_name
       AND ts.table_name = pt.table_name
    WHERE pt.pi_column_count = 0
      AND pic.table_name IS NULL

    UNION ALL

    SELECT
        pt.database_name
       ,pt.table_name
       ,pic.primary_index_columns
       ,COALESCE(ts.total_perm_bytes, 0)
       ,COALESCE(ts.skew_percent, 0)
       ,'PRIMARY_INDEX_NULLABLE_COLUMN' AS issue_code
       ,'Review nullable primary index columns; NULL-heavy PI values can concentrate rows on a small number of AMPs.' AS repair_hint
    FROM product_tables pt
    INNER JOIN primary_index_columns pic
        ON pic.database_name = pt.database_name
       AND pic.table_name = pt.table_name
    LEFT OUTER JOIN table_size ts
        ON ts.database_name = pt.database_name
       AND ts.table_name = pt.table_name
    WHERE pic.nullable_pi_column_count > 0

    UNION ALL

    SELECT
        pt.database_name
       ,pt.table_name
       ,pic.primary_index_columns
       ,COALESCE(ts.total_perm_bytes, 0)
       ,COALESCE(ts.skew_percent, 0)
       ,'PRIMARY_INDEX_LOW_CARDINALITY_SUSPECT' AS issue_code
       ,'Review low-cardinality-looking primary index columns and document the design if intentional.' AS repair_hint
    FROM product_tables pt
    INNER JOIN primary_index_columns pic
        ON pic.database_name = pt.database_name
       AND pic.table_name = pt.table_name
    LEFT OUTER JOIN table_size ts
        ON ts.database_name = pt.database_name
       AND ts.table_name = pt.table_name
    WHERE pic.primary_index_column_count = 1
      AND pic.suspicious_low_cardinality_name = 1

    UNION ALL

    SELECT
        pt.database_name
       ,pt.table_name
       ,COALESCE(pic.primary_index_columns, '') AS primary_index_columns
       ,COALESCE(ts.total_perm_bytes, 0)
       ,COALESCE(ts.skew_percent, 0)
       ,'PRIMARY_INDEX_SKEW_HIGH' AS issue_code
       ,'Review primary index choice because observed AMP storage skew is above the initial warning threshold.' AS repair_hint
    FROM product_tables pt
    INNER JOIN table_size ts
        ON ts.database_name = pt.database_name
       AND ts.table_name = pt.table_name
    LEFT OUTER JOIN primary_index_columns pic
        ON pic.database_name = pt.database_name
       AND pic.table_name = pt.table_name
    WHERE ts.total_perm_bytes > 0
      AND ts.amp_count > 1
      AND ts.skew_percent > 20
)
SELECT
    database_name
   ,table_name
   ,primary_index_columns
   ,total_perm_bytes
   ,skew_percent
   ,issue_code
   ,repair_hint
FROM primary_index_issues
ORDER BY issue_code, skew_percent DESC, total_perm_bytes DESC, database_name, table_name;
""".strip(),
            expected_result="Returns zero rows for product tables with suspicious primary index health.",
            repair_strategy=(
                "Review missing, nullable, low-cardinality-looking or highly skewed primary "
                "indexes. Document intentional designs; otherwise adjust the table or view-layer "
                "access strategy."
            ),
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
        TestCase(
            test_id=f"{prefix.upper()}-OPS-001",
            name="Observability module is registered and deployed",
            category=TestCategory.OPERATIONAL,
            severity=TestSeverity.WARNING,
            sql=f"""
WITH observability_module AS
(
    SELECT
        TRIM(database_name) AS observability_database
    FROM {sem_db}.data_product_map
    WHERE UPPER(TRIM(module_name)) = 'OBSERVABILITY'
      AND COALESCE(is_active, 1) = 1
),
observability_issues AS
(
    SELECT
        CAST(NULL AS VARCHAR(100)) AS observability_database
       ,'MISSING_OBSERVABILITY_MODULE' AS issue_code
       ,'No active Observability module is registered in data_product_map.' AS issue_detail
       ,'Register and deploy the Observability module so operational readiness can track lineage, freshness, quality and usage evidence.' AS repair_hint
    WHERE NOT EXISTS (SELECT 1 FROM observability_module)

    UNION ALL

    SELECT
        om.observability_database
       ,'OBSERVABILITY_DATABASE_NOT_DEPLOYED' AS issue_code
       ,'The registered Observability database is not visible in DBC.DatabasesV.' AS issue_detail
       ,'Deploy the Observability database or refresh data_product_map.database_name.' AS repair_hint
    FROM observability_module om
    LEFT OUTER JOIN DBC.DatabasesV dbv
        ON dbv.DatabaseName = om.observability_database
    WHERE dbv.DatabaseName IS NULL
)
SELECT
    observability_database
   ,issue_code
   ,issue_detail
   ,repair_hint
FROM observability_issues
ORDER BY issue_code, observability_database;
""".strip(),
            expected_result="Returns zero rows when an active Observability module is registered and deployed.",
            repair_strategy=(
                "Register and deploy the Observability module so the product can publish "
                "lineage, freshness, quality and usage evidence."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-OPS-002",
            name="Observability evidence objects are deployed",
            category=TestCategory.OPERATIONAL,
            severity=TestSeverity.WARNING,
            sql=f"""
WITH observability_module AS
(
    SELECT
        TRIM(database_name) AS observability_database
    FROM {sem_db}.data_product_map
    WHERE UPPER(TRIM(module_name)) = 'OBSERVABILITY'
      AND COALESCE(is_active, 1) = 1
),
required_tables AS
(
    SELECT 'change_event' AS object_name
    UNION ALL SELECT 'data_quality_metric'
    UNION ALL SELECT 'data_lineage'
    UNION ALL SELECT 'lineage_run'
),
required_semantic_views AS
(
    SELECT 'lineage_graph' AS object_name
    UNION ALL SELECT 'lineage_run_latest'
),
missing_table_issues AS
(
    SELECT
        om.observability_database
       ,rt.object_name
       ,'MISSING_OBSERVABILITY_TABLE' AS issue_code
       ,'Required Observability table is not deployed.' AS issue_detail
       ,'Deploy the Observability table so operational evidence can be captured.' AS repair_hint
    FROM observability_module om
    CROSS JOIN required_tables rt
    LEFT OUTER JOIN DBC.TablesV tv
        ON tv.DatabaseName = om.observability_database
       AND tv.TableName = rt.object_name
       AND tv.TableKind = 'T'
    WHERE tv.TableName IS NULL
),
missing_view_issues AS
(
    SELECT
        '{sem_db}' AS observability_database
       ,rsv.object_name
       ,'MISSING_OBSERVABILITY_SEMANTIC_VIEW' AS issue_code
       ,'Required Semantic observability view is not deployed.' AS issue_detail
       ,'Deploy the Semantic observability view so agents can inspect lineage and run status.' AS repair_hint
    FROM required_semantic_views rsv
    LEFT OUTER JOIN DBC.TablesV tv
        ON tv.DatabaseName = '{sem_db}'
       AND tv.TableName = rsv.object_name
       AND tv.TableKind = 'V'
    WHERE tv.TableName IS NULL
)
SELECT
    observability_database
   ,object_name
   ,issue_code
   ,issue_detail
   ,repair_hint
FROM missing_table_issues
UNION ALL
SELECT
    observability_database
   ,object_name
   ,issue_code
   ,issue_detail
   ,repair_hint
FROM missing_view_issues
ORDER BY issue_code, observability_database, object_name;
""".strip(),
            expected_result=(
                "Returns zero rows when required Observability evidence tables and Semantic "
                "observability views are deployed."
            ),
            repair_strategy=(
                "Deploy the Observability evidence tables and Semantic lineage views so agents "
                "can inspect operational health before relying on the product."
            ),
        ),
    ]
