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

NATIVE_VECTOR_PATTERN = re.compile(r"\b(TD_VECTORDISTANCE|VECTOR)\b", re.I)


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
        "repair_hint": "Use a fallback embedding recipe or mark native VECTOR unavailable.",
    }


def _row_uses_native_vector(row: dict[str, object]) -> bool:
    values = [
        row.get("recipe_title"),
        row.get("recipe_description"),
        row.get("use_case"),
        row.get("performance_notes"),
        row.get("sql_template"),
    ]
    return any(value is not None and NATIVE_VECTOR_PATTERN.search(str(value)) for value in values)


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
