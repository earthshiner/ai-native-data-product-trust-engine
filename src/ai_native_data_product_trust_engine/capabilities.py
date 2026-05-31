"""Capability discovery and alignment checks for data products."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ai_native_data_product_trust_engine.models import (
    ExpectedResult,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
)

NATIVE_VECTOR_PATTERN = re.compile(
    r"\b(TD_VECTORDISTANCE|NATIVE\s+VECTOR|VECTOR\s+(?:SEARCH|DISTANCE|INDEX|COLUMN))\b",
    re.I,
)
SEMANTIC_SEARCH_PATTERN = re.compile(
    r"\b(SEMANTIC\s+(?:SEARCH|SIMILARITY)|SIMILARITY\s+SEARCH|EMBEDDINGS?|"
    r"COSINE\s+(?:DISTANCE|SIMILARITY)|NEAREST\s+NEIGHBOU?R|RAG)\b",
    re.I,
)


class CapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ProductCapability:
    name: str
    status: CapabilityStatus
    evidence: list[dict[str, object]]


def run_capability_validations(prefix: str, adapter) -> list[TestResult]:
    native_vector = discover_native_vector_capability(prefix, adapter)
    fallback_embedding = discover_fallback_embedding_capability(prefix, adapter)
    return [
        _capability_inventory_result(prefix, native_vector, fallback_embedding),
        _native_vector_alignment_result(prefix, adapter, native_vector),
        _semantic_search_alignment_result(prefix, adapter, native_vector, fallback_embedding),
    ]


def capability_test_cases(prefix: str) -> list[TestCase]:
    return [
        TestCase(
            test_id=f"{prefix.upper()}-CAP-001",
            name="Product capability inventory is discoverable",
            category=TestCategory.CAPABILITY,
            severity=TestSeverity.INFO,
            sql=_native_vector_evidence_sql(prefix),
            expected_result="Records discovered capability status and physical evidence.",
            expected=ExpectedResult.NON_EMPTY,
            repair_strategy="Refresh Product_Capability metadata or physical capability evidence.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-CAP-002",
            name="Native VECTOR references align to deployed capability",
            category=TestCategory.CAPABILITY,
            severity=TestSeverity.CRITICAL,
            sql=_native_vector_reference_sql(prefix),
            expected_result="Returns zero native VECTOR references when native VECTOR is unavailable.",
            expected=ExpectedResult.ZERO_ROWS,
            repair_strategy="Use fallback embedding recipes or mark native VECTOR unavailable.",
        ),
        TestCase(
            test_id=f"{prefix.upper()}-CAP-003",
            name="Semantic search claims align to deployed capability",
            category=TestCategory.CAPABILITY,
            severity=TestSeverity.CRITICAL,
            sql=_semantic_search_reference_sql(prefix),
            expected_result=(
                "Returns zero semantic-search metadata claims that lack native VECTOR or "
                "fallback embedding evidence."
            ),
            expected=ExpectedResult.ZERO_ROWS,
            repair_strategy=(
                "Update semantic-search wording, add supported fallback recipes, or deploy the "
                "advertised native VECTOR capability."
            ),
        ),
    ]


def discover_native_vector_capability(prefix: str, adapter) -> ProductCapability:
    evidence = adapter.fetch_all(_native_vector_evidence_sql(prefix))
    return ProductCapability(
        name="NATIVE_VECTOR",
        status=CapabilityStatus.AVAILABLE if evidence else CapabilityStatus.UNAVAILABLE,
        evidence=evidence[:10],
    )


def discover_fallback_embedding_capability(prefix: str, adapter) -> ProductCapability:
    evidence = adapter.fetch_all(_fallback_embedding_evidence_sql(prefix))
    return ProductCapability(
        name="FALLBACK_EMBEDDING",
        status=CapabilityStatus.AVAILABLE if evidence else CapabilityStatus.UNAVAILABLE,
        evidence=evidence[:10],
    )


def _capability_inventory_result(
    prefix: str,
    native_vector: ProductCapability,
    fallback_embedding: ProductCapability,
) -> TestResult:
    test_case = capability_test_cases(prefix)[0]
    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED,
        row_count=2,
        sample_rows=[
            _capability_row(native_vector),
            _capability_row(fallback_embedding),
        ],
    )


def _native_vector_alignment_result(
    prefix: str,
    adapter,
    native_vector: ProductCapability,
) -> TestResult:
    test_case = capability_test_cases(prefix)[1]
    try:
        reference_rows = adapter.fetch_all(test_case.sql)
    except Exception as exc:  # noqa: BLE001 - adapters normalise backend errors later.
        return TestResult(
            test_case=test_case,
            status=TestStatus.ERROR,
            row_count=0,
            error_message=str(exc),
        )

    findings = []
    if native_vector.status == CapabilityStatus.UNAVAILABLE:
        findings = [_native_vector_finding(row) for row in reference_rows if _row_uses_native_vector(row)]

    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED if not findings else TestStatus.FAILED,
        row_count=len(findings),
        sample_rows=findings[:10],
    )


def _semantic_search_alignment_result(
    prefix: str,
    adapter,
    native_vector: ProductCapability,
    fallback_embedding: ProductCapability,
) -> TestResult:
    test_case = capability_test_cases(prefix)[2]
    try:
        reference_rows = adapter.fetch_all(test_case.sql)
    except Exception as exc:  # noqa: BLE001 - adapters normalise backend errors later.
        return TestResult(
            test_case=test_case,
            status=TestStatus.ERROR,
            row_count=0,
            error_message=str(exc),
        )

    findings: list[dict[str, object]] = []
    for row in reference_rows:
        if not _row_mentions_semantic_search(row):
            continue
        if _row_uses_native_vector(row) and native_vector.status == CapabilityStatus.UNAVAILABLE:
            findings.append(_native_vector_finding(row))
        elif (
            native_vector.status == CapabilityStatus.UNAVAILABLE
            and fallback_embedding.status == CapabilityStatus.UNAVAILABLE
        ):
            findings.append(_semantic_search_unavailable_finding(row))

    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED if not findings else TestStatus.FAILED,
        row_count=len(findings),
        sample_rows=findings[:10],
    )


def _capability_row(capability: ProductCapability) -> dict[str, object]:
    return {
        "capability": capability.name,
        "status": capability.status.value,
        "evidence_count": len(capability.evidence),
        "evidence": capability.evidence,
    }


def _native_vector_finding(row: dict[str, object]) -> dict[str, object]:
    return {
        "issue_code": "UNSUPPORTED_CAPABILITY",
        "capability": "NATIVE_VECTOR",
        "unsupported_feature": "TD_VECTORDISTANCE",
        "recipe_id": row.get("recipe_id"),
        "recipe_title": row.get("recipe_title"),
        "source_table": row.get("source_table"),
        "source_column": row.get("source_column"),
        "row_key": row.get("row_key"),
        "repair_hint": (
            "Use fallback embedding wording/recipes, deploy native VECTOR support, or mark "
            "native VECTOR unavailable."
        ),
    }


def _semantic_search_unavailable_finding(row: dict[str, object]) -> dict[str, object]:
    return {
        "issue_code": "SEMANTIC_SEARCH_CAPABILITY_UNAVAILABLE",
        "capability": "SEMANTIC_SEARCH",
        "source_table": row.get("source_table"),
        "source_column": row.get("source_column"),
        "row_key": row.get("row_key"),
        "recipe_id": row.get("recipe_id"),
        "recipe_title": row.get("recipe_title"),
        "repair_hint": (
            "Remove semantic-search claims, deploy native VECTOR or fallback embedding storage, "
            "or route users to a supported non-semantic recipe."
        ),
    }


def _row_uses_native_vector(row: dict[str, object]) -> bool:
    return _row_matches(row, NATIVE_VECTOR_PATTERN)


def _row_mentions_semantic_search(row: dict[str, object]) -> bool:
    return _row_matches(row, SEMANTIC_SEARCH_PATTERN) or _row_uses_native_vector(row)


def _row_matches(row: dict[str, object], pattern: re.Pattern[str]) -> bool:
    values = [
        row.get("recipe_title"),
        row.get("recipe_description"),
        row.get("use_case"),
        row.get("performance_notes"),
        row.get("sql_template"),
        row.get("text_value"),
    ]
    return any(value is not None and pattern.search(str(value)) for value in values)


def _native_vector_evidence_sql(prefix: str) -> str:
    return f"""
SELECT
    DatabaseName
   ,TableName
   ,ColumnName
   ,ColumnType
FROM DBC.ColumnsV
WHERE DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
  AND (
       UPPER(ColumnType) IN ('VECTOR', 'VE')
    OR UPPER(ColumnName) = 'VECTOR'
  )
""".strip()


def _fallback_embedding_evidence_sql(prefix: str) -> str:
    return f"""
SELECT
    DatabaseName
   ,TableName
   ,ColumnName
   ,ColumnType
FROM DBC.ColumnsV
WHERE DatabaseName LIKE '{prefix}\\_%' ESCAPE '\\'
  AND (
       UPPER(TableName) LIKE '%EMBED%'
    OR UPPER(ColumnName) LIKE '%EMBED%'
  )
""".strip()


def _native_vector_reference_sql(prefix: str) -> str:
    return f"""
SELECT
    recipe_id
   ,recipe_title
   ,recipe_description
   ,use_case
   ,performance_notes
   ,sql_template
FROM {prefix}_MEM_STD_V.Query_Cookbook
WHERE COALESCE(is_active, 1) = 1
""".strip()


def _semantic_search_reference_sql(prefix: str) -> str:
    sem_db = f"{prefix}_SEM_STD_V"
    mem_db = f"{prefix}_MEM_STD_V"
    return f"""
SELECT
    'Query_Cookbook' AS source_table
   ,'recipe_title' AS source_column
   ,recipe_id AS row_key
   ,recipe_id
   ,recipe_title
   ,CAST(recipe_title AS VARCHAR(32000)) AS text_value
FROM {mem_db}.Query_Cookbook
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'Query_Cookbook'
   ,'recipe_description'
   ,recipe_id
   ,recipe_id
   ,recipe_title
   ,CAST(recipe_description AS VARCHAR(32000))
FROM {mem_db}.Query_Cookbook
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'Query_Cookbook'
   ,'use_case'
   ,recipe_id
   ,recipe_id
   ,recipe_title
   ,CAST(use_case AS VARCHAR(32000))
FROM {mem_db}.Query_Cookbook
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'Query_Cookbook'
   ,'performance_notes'
   ,recipe_id
   ,recipe_id
   ,recipe_title
   ,CAST(performance_notes AS VARCHAR(32000))
FROM {mem_db}.Query_Cookbook
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'Query_Cookbook'
   ,'sql_template'
   ,recipe_id
   ,recipe_id
   ,recipe_title
   ,CAST(sql_template AS VARCHAR(32000))
FROM {mem_db}.Query_Cookbook
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'entity_metadata'
   ,'entity_description'
   ,CAST(entity_metadata_id AS VARCHAR(128))
   ,CAST(NULL AS VARCHAR(50))
   ,CAST(NULL AS VARCHAR(200))
   ,CAST(entity_description AS VARCHAR(32000))
FROM {sem_db}.entity_metadata
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'column_metadata'
   ,'business_description'
   ,CAST(column_metadata_id AS VARCHAR(128))
   ,CAST(NULL AS VARCHAR(50))
   ,CAST(NULL AS VARCHAR(200))
   ,CAST(business_description AS VARCHAR(32000))
FROM {sem_db}.column_metadata
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'table_relationship'
   ,'relationship_description'
   ,CAST(relationship_id AS VARCHAR(128))
   ,CAST(NULL AS VARCHAR(50))
   ,CAST(NULL AS VARCHAR(200))
   ,CAST(relationship_description AS VARCHAR(32000))
FROM {sem_db}.table_relationship
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'table_relationship'
   ,'relationship_meaning'
   ,CAST(relationship_id AS VARCHAR(128))
   ,CAST(NULL AS VARCHAR(50))
   ,CAST(NULL AS VARCHAR(200))
   ,CAST(relationship_meaning AS VARCHAR(32000))
FROM {sem_db}.table_relationship
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'Business_Glossary'
   ,'definition'
   ,term
   ,CAST(NULL AS VARCHAR(50))
   ,CAST(NULL AS VARCHAR(200))
   ,CAST(definition AS VARCHAR(32000))
FROM {mem_db}.Business_Glossary
WHERE COALESCE(is_active, 1) = 1
UNION ALL
SELECT
    'Business_Glossary'
   ,'business_context'
   ,term
   ,CAST(NULL AS VARCHAR(50))
   ,CAST(NULL AS VARCHAR(200))
   ,CAST(business_context AS VARCHAR(32000))
FROM {mem_db}.Business_Glossary
WHERE COALESCE(is_active, 1) = 1
""".strip()
