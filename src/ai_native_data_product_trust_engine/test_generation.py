"""Generate deterministic trust tests from a data product prefix."""

from __future__ import annotations

from ai_native_data_product_trust_engine.object_filters import backup_object_exclusion_sql
from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    TestCase,
    TestCategory,
    TestSeverity,
)


def semantic_database(prefix: str) -> str:
    return f"{prefix}_SEM_STD_V"


def semantic_business_view_database(prefix: str) -> str:
    return f"{prefix}_SEM_BUS_V"


def observability_view_database(prefix: str) -> str:
    return f"{prefix}_OBS_STD_V"


def business_view_database(physical_database_expression: str) -> str:
    return (
        f"OREPLACE(OREPLACE({physical_database_expression}, '_STD_T', '_BUS_V'), "
        "'_STD_V', '_BUS_V')"
    )


def memory_database(prefix: str) -> str:
    return f"{prefix}_MEM_STD_V"


def data_products_registry_database() -> str:
    return "DataProductsMaster_GOV_BUS_V"


def data_products_registry_view() -> str:
    return "active_data_product_registry"


def deployed_module_database_filter(sem_db: str, database_expression: str) -> str:
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


def generate_metadata_tests(prefix: str) -> list[TestCase]:
    sem_db = semantic_database(prefix)
    mem_db = memory_database(prefix)
    registry_db = data_products_registry_database()
    registry_view = data_products_registry_view()

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
  AND COALESCE(em.is_active, 1) = 1
  AND {deployed_module_database_filter(sem_db, 'em.database_name')}
  AND {backup_object_exclusion_sql('em.table_name')};
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
  AND COALESCE(cmeta.is_active, 1) = 1
  AND {deployed_module_database_filter(sem_db, 'cmeta.database_name')}
  AND {backup_object_exclusion_sql('cmeta.table_name')};
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
  AND {deployed_module_database_filter(sem_db, 'tr.source_database')}
  AND {deployed_module_database_filter(sem_db, 'tr.target_database')}
  AND {backup_object_exclusion_sql('tr.source_table')}
  AND {backup_object_exclusion_sql('tr.target_table')}
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
      AND {deployed_module_database_filter(sem_db, 'tr.source_database')}
      AND {deployed_module_database_filter(sem_db, 'tr.target_database')}
      AND {backup_object_exclusion_sql('tr.source_table')}
      AND {backup_object_exclusion_sql('tr.target_table')}
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
      AND {deployed_module_database_filter(sem_db, 'colv.DatabaseName')}
      AND {backup_object_exclusion_sql('colv.TableName')}
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
            test_id=f"{prefix.upper()}-SEM-005",
            name="Column metadata datatypes match deployed columns",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.WARNING,
            sql=f"""
SELECT
    cmeta.database_name
   ,cmeta.table_name
   ,cmeta.column_name
   ,cmeta.data_type AS metadata_data_type
   ,TRIM(colv.ColumnType) AS deployed_column_type
   ,'COLUMN_METADATA_DATATYPE_MISMATCH' AS issue_code
   ,'Refresh column_metadata.data_type from DBC.ColumnsV.' AS repair_hint
FROM {sem_db}.column_metadata cmeta
INNER JOIN DBC.ColumnsV colv
    ON colv.DatabaseName = cmeta.database_name
   AND colv.TableName = cmeta.table_name
   AND colv.ColumnName = cmeta.column_name
WHERE COALESCE(cmeta.is_active, 1) = 1
  AND {deployed_module_database_filter(sem_db, 'cmeta.database_name')}
  AND {backup_object_exclusion_sql('cmeta.table_name')}
  AND UPPER(cmeta.data_type) NOT LIKE
    CASE TRIM(colv.ColumnType)
        WHEN 'I' THEN '%INTEGER%'
        WHEN 'I1' THEN '%BYTEINT%'
        WHEN 'I2' THEN '%SMALLINT%'
        WHEN 'I8' THEN '%BIGINT%'
        WHEN 'D' THEN '%DECIMAL%'
        WHEN 'F' THEN '%FLOAT%'
        WHEN 'DA' THEN '%DATE%'
        WHEN 'TS' THEN '%TIMESTAMP%'
        WHEN 'TZ' THEN '%TIME%'
        WHEN 'AT' THEN '%TIME%'
        WHEN 'CV' THEN '%VARCHAR%'
        WHEN 'CF' THEN '%CHAR%'
        WHEN 'CO' THEN '%CLOB%'
        WHEN 'BO' THEN '%BLOB%'
        ELSE '%' || UPPER(TRIM(colv.ColumnType)) || '%'
    END
ORDER BY cmeta.database_name, cmeta.table_name, cmeta.column_name;
""".strip(),
            expected_result="Returns zero rows when semantic column datatypes match deployed columns.",
            repair_strategy="Refresh column_metadata.data_type from the deployed column catalogue.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-006",
            name="Column metadata covers active entity columns",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.WARNING,
            sql=f"""
SELECT
    em.database_name
   ,em.table_name
   ,colv.ColumnName AS column_name
   ,'MISSING_COLUMN_METADATA' AS issue_code
   ,'Add active column_metadata for this deployed entity column.' AS repair_hint
FROM {sem_db}.entity_metadata em
INNER JOIN DBC.ColumnsV colv
    ON colv.DatabaseName = em.database_name
   AND colv.TableName = em.table_name
LEFT OUTER JOIN {sem_db}.column_metadata cmeta
    ON cmeta.database_name = colv.DatabaseName
   AND cmeta.table_name = colv.TableName
   AND cmeta.column_name = colv.ColumnName
   AND COALESCE(cmeta.is_active, 1) = 1
WHERE COALESCE(em.is_active, 1) = 1
  AND {deployed_module_database_filter(sem_db, 'em.database_name')}
  AND {backup_object_exclusion_sql('em.table_name')}
  AND {backup_object_exclusion_sql('colv.TableName')}
  AND cmeta.column_name IS NULL
ORDER BY em.database_name, em.table_name, colv.ColumnId;
""".strip(),
            expected_result="Returns zero rows when active entities have metadata for every deployed column.",
            repair_strategy="Generate or refresh column_metadata for active entity columns.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-007",
            name="Data product primary view names resolve to deployed views",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
WITH module_primary_views AS
(
    SELECT
        dpm.module_name
       ,TRIM(token.token) AS primary_view_reference
       ,CASE
            WHEN INDEX(TRIM(token.token), '.') > 0
                THEN SUBSTR(TRIM(token.token), 1, INDEX(TRIM(token.token), '.') - 1)
            ELSE NULL
        END AS declared_database_name
       ,CASE
            WHEN INDEX(TRIM(token.token), '.') > 0
                THEN SUBSTR(TRIM(token.token), INDEX(TRIM(token.token), '.') + 1)
            ELSE TRIM(token.token)
        END AS primary_view_name
    FROM {sem_db}.data_product_map dpm,
    TABLE (
        STRTOK_SPLIT_TO_TABLE(CAST(dpm.module_id AS VARCHAR(128)), dpm.primary_views, ',')
        RETURNS (
            module_id VARCHAR(128) CHARACTER SET UNICODE
           ,tokennum INTEGER
           ,token VARCHAR(256) CHARACTER SET UNICODE
        )
    ) AS token
    WHERE COALESCE(dpm.is_active, 1) = 1
      AND UPPER(COALESCE(TRIM(dpm.deployment_status), 'DEPLOYED')) = 'DEPLOYED'
      AND COALESCE(dpm.primary_views, '') NOT IN ('', 'None')
)
SELECT
    module_name
   ,primary_view_reference
   ,declared_database_name
   ,primary_view_name
   ,'PRIMARY_VIEW_NAME_NOT_DEPLOYED' AS issue_code
   ,'Update data_product_map.primary_views to an existing view name. Qualify the name as database.view when the database must be explicit.' AS repair_hint
FROM module_primary_views mpv
WHERE NOT EXISTS
(
    SELECT 1
    FROM DBC.TablesV tv
    WHERE tv.TableName = mpv.primary_view_name
      AND tv.TableKind IN ('V', 'O', 'Q')
      AND (
          tv.DatabaseName = mpv.declared_database_name
          OR (
              mpv.declared_database_name IS NULL
              AND tv.DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
          )
      )
)
ORDER BY module_name, primary_view_reference;
""".strip(),
            expected_result="Returns zero rows when each primary view name resolves to a deployed product view.",
            repair_strategy=(
                "Refresh data_product_map.primary_views with deployed view names. Use "
                "database.view references when views are stored outside the module database."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-008",
            name="Entity metadata publishes BUS_V view names",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
WITH active_entities AS
(
    SELECT
        em.entity_metadata_id
       ,em.entity_name
       ,em.database_name
       ,em.table_name
       ,em.view_name
       ,{business_view_database('em.database_name')} AS business_database_name
    FROM {sem_db}.entity_metadata em
    WHERE COALESCE(em.is_active, 1) = 1
      AND {deployed_module_database_filter(sem_db, 'em.database_name')}
      AND {backup_object_exclusion_sql('em.table_name')}
)
SELECT
    entity_metadata_id
   ,entity_name
   ,business_database_name
   ,view_name
   ,CASE
        WHEN COALESCE(view_name, '') IN ('', 'None') THEN 'ENTITY_VIEW_NAME_MISSING'
        ELSE 'ENTITY_VIEW_NAME_NOT_DEPLOYED'
    END AS issue_code
   ,'Populate entity_metadata.view_name with the approved BUS_V view for agent access.' AS repair_hint
FROM active_entities ae
LEFT OUTER JOIN DBC.TablesV tv
    ON tv.DatabaseName = ae.business_database_name
   AND tv.TableName = ae.view_name
   AND tv.TableKind IN ('V', 'O', 'Q')
WHERE COALESCE(ae.view_name, '') IN ('', 'None')
   OR tv.TableName IS NULL
ORDER BY entity_name, business_database_name, view_name;
""".strip(),
            expected_result="Returns zero rows when active entities point agents at deployed BUS_V views.",
            repair_strategy="Populate entity_metadata.view_name and deploy the referenced BUS_V views.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-009",
            name="Entity deleted flag metadata is populated and deployed",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.WARNING,
            sql=f"""
SELECT
    em.entity_metadata_id
   ,em.entity_name
   ,em.database_name
   ,em.table_name
   ,em.deleted_flag_column
   ,CASE
        WHEN COALESCE(em.deleted_flag_column, '') IN ('', 'None')
            THEN 'ENTITY_DELETED_FLAG_MISSING'
        ELSE 'ENTITY_DELETED_FLAG_NOT_DEPLOYED'
    END AS issue_code
   ,'Populate deleted_flag_column where delete tracking exists, or explicitly mark the entity as not delete-tracked.' AS repair_hint
FROM {sem_db}.entity_metadata em
LEFT OUTER JOIN DBC.ColumnsV colv
    ON colv.DatabaseName = em.database_name
   AND colv.TableName = em.table_name
   AND colv.ColumnName = em.deleted_flag_column
WHERE COALESCE(em.is_active, 1) = 1
  AND {deployed_module_database_filter(sem_db, 'em.database_name')}
  AND COALESCE(em.temporal_pattern, '') NOT IN ('', 'None')
  AND {backup_object_exclusion_sql('em.table_name')}
  AND (
      COALESCE(em.deleted_flag_column, '') IN ('', 'None')
      OR colv.ColumnName IS NULL
  )
ORDER BY em.entity_name, em.database_name, em.table_name;
""".strip(),
            expected_result="Returns zero rows when temporal entity delete flags are complete and valid.",
            repair_strategy="Refresh deleted_flag_column metadata or document entities without delete tracking.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-010",
            name="Relationship metadata uses BUS_V access endpoints",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
SELECT
    tr.relationship_name
   ,tr.source_database AS database_name
   ,tr.source_table AS object_name
   ,tr.source_column AS column_name
   ,'RELATIONSHIP_SOURCE_NOT_BUS_V' AS issue_code
   ,'Update source_database/source_table to the approved BUS_V database and view.' AS repair_hint
FROM {sem_db}.table_relationship tr
WHERE COALESCE(tr.is_active, 1) = 1
  AND {deployed_module_database_filter(sem_db, 'tr.source_database')}
  AND {backup_object_exclusion_sql('tr.source_table')}
  AND UPPER(tr.source_database) NOT LIKE '%\\_BUS\\_V' ESCAPE '\\'
UNION ALL
SELECT
    tr.relationship_name
   ,tr.target_database AS database_name
   ,tr.target_table AS object_name
   ,tr.target_column AS column_name
   ,'RELATIONSHIP_TARGET_NOT_BUS_V' AS issue_code
   ,'Update target_database/target_table to the approved BUS_V database and view.' AS repair_hint
FROM {sem_db}.table_relationship tr
WHERE COALESCE(tr.is_active, 1) = 1
  AND {deployed_module_database_filter(sem_db, 'tr.target_database')}
  AND {backup_object_exclusion_sql('tr.target_table')}
  AND UPPER(tr.target_database) NOT LIKE '%\\_BUS\\_V' ESCAPE '\\'
ORDER BY relationship_name, issue_code, database_name, object_name, column_name;
""".strip(),
            expected_result="Returns zero rows when generated joins use governed BUS_V endpoints.",
            repair_strategy="Move relationship metadata to BUS_V databases and view names for agent access.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-011",
            name="Lineage access view is deployed",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.WARNING,
            sql=f"""
SELECT
    '{semantic_business_view_database(prefix)}' AS database_name
   ,'data_lineage' AS object_name
   ,'MISSING_LINEAGE_ACCESS_VIEW' AS issue_code
   ,'The lineage access view is not deployed, so lineage endpoint metadata cannot be validated.' AS issue_detail
   ,'Deploy {semantic_business_view_database(prefix)}.data_lineage before validating lineage access endpoints.' AS repair_hint
WHERE NOT EXISTS (
    SELECT 1
    FROM DBC.TablesV tv
    WHERE tv.DatabaseName = '{semantic_business_view_database(prefix)}'
      AND tv.TableName = 'data_lineage'
      AND tv.TableKind IN ('V', 'O', 'Q')
);
""".strip(),
            expected_result=(
                "Returns zero rows when the Semantic BUS_V lineage access view is deployed."
            ),
            repair_strategy=(
                "Deploy the Semantic BUS_V lineage access view before validating or publishing "
                "agent-facing lineage metadata."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-012",
            name="Deployed module objects have comments",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.WARNING,
            sql=f"""
SELECT
    TRIM(tv.DatabaseName) AS database_name
   ,TRIM(tv.TableName) AS object_name
   ,TRIM(tv.TableKind) AS object_kind
   ,'MISSING_OBJECT_COMMENT' AS issue_code
   ,'Add a COMMENT statement describing the object purpose and governed usage.' AS repair_hint
FROM DBC.TablesV tv
WHERE tv.DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
  AND {deployed_module_database_filter(sem_db, 'tv.DatabaseName')}
  AND {backup_object_exclusion_sql('tv.TableName')}
  AND COALESCE(TRIM(tv.CommentString), '') = ''
ORDER BY tv.DatabaseName, tv.TableName;
""".strip(),
            expected_result="Returns zero rows when every deployed module object has a comment.",
            repair_strategy=(
                "Add an object-level COMMENT statement describing each deployed object's purpose, "
                "ownership and governed usage."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-SEM-013",
            name="Deployed module columns have comments",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.WARNING,
            sql=f"""
SELECT
    TRIM(colv.DatabaseName) AS database_name
   ,TRIM(colv.TableName) AS object_name
   ,TRIM(colv.ColumnName) AS column_name
   ,'MISSING_COLUMN_COMMENT' AS issue_code
   ,'Add a COMMENT statement describing the column meaning and expected usage.' AS repair_hint
FROM DBC.ColumnsV colv
WHERE colv.DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
  AND {deployed_module_database_filter(sem_db, 'colv.DatabaseName')}
  AND {backup_object_exclusion_sql('colv.TableName')}
  AND COALESCE(TRIM(colv.CommentString), '') = ''
ORDER BY colv.DatabaseName, colv.TableName, colv.ColumnId;
""".strip(),
            expected_result="Returns zero rows when every deployed module column has a comment.",
            repair_strategy=(
                "Add a column-level COMMENT statement describing the business meaning, format and "
                "governed usage of each deployed column."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-DISCOVERY-001",
            name="Central active data product registry view exists",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.CRITICAL,
            sql=f"""
SELECT
    '{registry_db}' AS database_name
   ,'{registry_view}' AS view_name
   ,'MISSING_ACTIVE_DATA_PRODUCT_REGISTRY_VIEW' AS issue_code
   ,'{registry_db}.{registry_view} is required for the Data Product Orientation Layer.' AS issue_detail
   ,'Create or grant access to the central active data product registry view before publishing MCP discovery resources.' AS repair_hint
WHERE NOT EXISTS (
    SELECT 1
    FROM DBC.TablesV tv
    WHERE tv.DatabaseName = '{registry_db}'
      AND tv.TableName = '{registry_view}'
      AND tv.TableKind IN ('V', 'O', 'Q')
);
""".strip(),
            expected_result=f"Returns zero rows when {registry_db}.{registry_view} exists.",
            repair_strategy=(
                f"Create or grant access to {registry_db}.{registry_view} so the MCP Data Product "
                "Orientation Layer can discover active products from the governed registry."
            ),
        ),
        TestCase(
            test_id=f"{prefix.upper()}-DISCOVERY-002",
            name="Central registry matches orientation metadata",
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
       ,semantic_view_database
       ,memory_database
       ,memory_view_database
       ,observability_database
       ,observability_view_database
       ,manifest_json
       ,contract_uri
       ,semantic_uri
       ,quality_uri
       ,lineage_uri
       ,policy_uri
       ,approved_entrypoint
       ,approved_access_mode
    FROM {registry_db}.{registry_view}
    WHERE UPPER(TRIM(product_status)) = 'ACTIVE'
      AND (
          UPPER(TRIM(product_id)) = UPPER('{prefix}')
          OR UPPER(TRIM(product_name)) LIKE UPPER('{prefix}%')
          OR semantic_database = '{sem_db}'
          OR semantic_view_database = '{sem_db}'
      )
),
module_map AS
(
    SELECT
        UPPER(TRIM(module_name)) AS module_name
       ,TRIM(database_name) AS database_name
       ,OREPLACE(OREPLACE(TRIM(database_name), '_STD_T', '_STD_V'), '_BUS_V', '_STD_V')
            AS standard_view_database_name
       ,OREPLACE(OREPLACE(TRIM(database_name), '_STD_T', '_BUS_V'), '_STD_V', '_BUS_V')
            AS business_view_database_name
    FROM {sem_db}.data_product_map
    WHERE COALESCE(is_active, 1) = 1
),
registry_issues AS
(
    SELECT
        CAST(NULL AS VARCHAR(128)) AS product_id
       ,'MISSING_PRODUCT_REGISTRY_ROW' AS issue_code
       ,'No active central registry row points to this data product.' AS issue_detail
       ,'Insert or refresh {registry_db}.{registry_view} for this data product.' AS repair_hint
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
       ,'Refresh active_data_product_registry.semantic_database or Semantic.data_product_map.' AS repair_hint
    FROM active_registry ar
    WHERE NOT EXISTS (
        SELECT 1
        FROM module_map mm
        WHERE mm.module_name = 'SEMANTIC'
          AND (
              mm.database_name = ar.semantic_database
              OR mm.database_name = ar.semantic_view_database
              OR mm.standard_view_database_name = ar.semantic_database
              OR mm.standard_view_database_name = ar.semantic_view_database
              OR mm.business_view_database_name = ar.semantic_database
              OR mm.business_view_database_name = ar.semantic_view_database
          )
    )

    UNION ALL

    SELECT
        ar.product_id
       ,'MEMORY_DATABASE_NOT_VIEW_LAYER' AS issue_code
       ,'Registry memory_database points to a physical table database instead of a governed view database.' AS issue_detail
       ,'Set active_data_product_registry.memory_database to the Memory STD_V or BUS_V database.' AS repair_hint
    FROM active_registry ar
    WHERE ar.memory_database IS NOT NULL
      AND UPPER(TRIM(ar.memory_database)) NOT LIKE '%\\_STD\\_V' ESCAPE '\\'
      AND UPPER(TRIM(ar.memory_database)) NOT LIKE '%\\_BUS\\_V' ESCAPE '\\'

    UNION ALL

    SELECT
        ar.product_id
       ,'MEMORY_DATABASE_NOT_IN_MODULE_MAP' AS issue_code
       ,'Registry memory_database does not match a governed view database for an active Memory module in data_product_map.' AS issue_detail
       ,'Set active_data_product_registry.memory_database to the Memory STD_V or BUS_V database.' AS repair_hint
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
          AND (
              mm.standard_view_database_name = ar.memory_database
              OR mm.standard_view_database_name = ar.memory_view_database
              OR mm.business_view_database_name = ar.memory_database
              OR mm.business_view_database_name = ar.memory_view_database
          )
    )

    UNION ALL

    SELECT
        ar.product_id
       ,'OBSERVABILITY_DATABASE_NOT_IN_MODULE_MAP' AS issue_code
       ,'Registry observability_database does not match an active Observability module in data_product_map.' AS issue_detail
       ,'Refresh active_data_product_registry.observability_database or Semantic.data_product_map.' AS repair_hint
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
          AND (
              mm.database_name = ar.observability_database
              OR mm.database_name = ar.observability_view_database
              OR mm.standard_view_database_name = ar.observability_database
              OR mm.standard_view_database_name = ar.observability_view_database
              OR mm.business_view_database_name = ar.observability_database
              OR mm.business_view_database_name = ar.observability_view_database
          )
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
            expected_result=(
                "Returns zero rows when the central registry and orientation manifest match "
                "deployed metadata."
            ),
            repair_strategy=(
                f"Refresh {registry_db}.{registry_view} and its manifest_json so MCP clients discover "
                "the product, contract, semantic model, policy, quality, lineage and approved access "
                "path before querying data."
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
      AND {deployed_module_database_filter(sem_db, 'tsv.DatabaseName')}
      AND {backup_object_exclusion_sql('tsv.TableName')}
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
      AND {deployed_module_database_filter(sem_db, 'tv.DatabaseName')}
      AND {backup_object_exclusion_sql('tv.TableName')}
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
    WHERE {deployed_module_database_filter(sem_db, 'tsv.DatabaseName')}
      AND {backup_object_exclusion_sql('tsv.TableName')}
    GROUP BY TRIM(tsv.DatabaseName), TRIM(tsv.TableName)
),
primary_index_column_rows AS
(
    SELECT
        TRIM(iv.DatabaseName) AS database_name
       ,TRIM(iv.TableName) AS table_name
       ,TRIM(iv.ColumnName) AS column_name
       ,iv.ColumnPosition AS column_position
       ,COALESCE(colv.Nullable, 'N') AS nullable_flag
    FROM DBC.IndicesV iv
    LEFT OUTER JOIN DBC.ColumnsV colv
        ON colv.DatabaseName = iv.DatabaseName
       AND colv.TableName = iv.TableName
       AND colv.ColumnName = iv.ColumnName
    WHERE iv.IndexNumber = 1
      AND iv.IndexType IN ('P', 'Q', 'A', 'K')
      AND iv.DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
      AND {deployed_module_database_filter(sem_db, 'iv.DatabaseName')}
      AND {backup_object_exclusion_sql('iv.TableName')}
),
primary_index_columns AS
(
    SELECT
        database_name
       ,table_name
       ,LISTAGG(column_name, ',') WITHIN GROUP (ORDER BY column_position)
            AS primary_index_columns
       ,COUNT(*) AS primary_index_column_count
       ,SUM(CASE WHEN nullable_flag = 'Y' THEN 1 ELSE 0 END) AS nullable_pi_column_count
       ,MAX(
            CASE
                WHEN UPPER(column_name) LIKE '%STATUS%'
                  OR UPPER(column_name) LIKE '%TYPE%'
                  OR UPPER(column_name) LIKE '%FLAG%'
                  OR UPPER(column_name) LIKE '%GENDER%'
                  OR UPPER(column_name) LIKE '%CATEGORY%'
                THEN 1
                ELSE 0
            END
        ) AS suspicious_low_cardinality_name
    FROM primary_index_column_rows
    GROUP BY database_name, table_name
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
      AND {deployed_module_database_filter(sem_db, 'tr.source_database')}
      AND tr.source_database IS NOT NULL
      AND tr.source_table IS NOT NULL
      AND tr.source_column IS NOT NULL
      AND {backup_object_exclusion_sql('tr.source_table')}
    UNION
    SELECT DISTINCT
        tr.target_database AS database_name
       ,tr.target_table AS table_name
       ,tr.target_column AS column_name
       ,tr.relationship_name
       ,'RELATIONSHIP_TARGET_JOIN' AS usage_type
    FROM {sem_db}.table_relationship tr
    WHERE COALESCE(tr.is_active, 1) = 1
      AND {deployed_module_database_filter(sem_db, 'tr.target_database')}
      AND tr.target_database IS NOT NULL
      AND tr.target_table IS NOT NULL
      AND tr.target_column IS NOT NULL
      AND {backup_object_exclusion_sql('tr.target_table')}
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
      AND {backup_object_exclusion_sql('statv.TableName')}
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
        TestCase(
            test_id=f"{prefix.upper()}-OPS-003",
            name="Observability BUS_V access views are deployed",
            category=TestCategory.OPERATIONAL,
            severity=TestSeverity.CRITICAL,
            sql=f"""
WITH required_observability_views AS
(
    SELECT 'change_event' AS object_name
    UNION ALL SELECT 'data_quality_metric'
    UNION ALL SELECT 'lineage_run'
    UNION ALL SELECT 'model_performance'
    UNION ALL SELECT 'agent_outcome'
)
SELECT
    '{prefix}_OBS_BUS_V' AS database_name
   ,rov.object_name
   ,'MISSING_OBSERVABILITY_BUS_VIEW' AS issue_code
   ,'Required Observability BUS_V view is not deployed for governed agent access.' AS issue_detail
   ,'Create the Observability BUS_V database and expose this object as a governed access view.' AS repair_hint
FROM required_observability_views rov
LEFT OUTER JOIN DBC.TablesV tv
    ON tv.DatabaseName = '{prefix}_OBS_BUS_V'
   AND tv.TableName = rov.object_name
   AND tv.TableKind IN ('V', 'O', 'Q')
WHERE tv.TableName IS NULL
ORDER BY rov.object_name;
""".strip(),
            expected_result=(
                "Returns zero rows when the Observability module has governed BUS_V access views."
            ),
            repair_strategy=(
                "Create the Observability BUS_V database and publish governed views for "
                "observability objects consumed by agents."
            ),
        ),
    ]
