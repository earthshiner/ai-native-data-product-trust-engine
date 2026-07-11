"""Detect object-like references inside free-text metadata."""

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


class ReferenceClassification(str, Enum):
    STALE_ALIAS = "STALE_ALIAS"
    TYPO_SUSPECT = "TYPO_SUSPECT"


@dataclass(frozen=True)
class MigrationRule:
    old_token: str
    new_token: str
    classification: ReferenceClassification
    safe_auto_apply: bool


@dataclass(frozen=True)
class TextReferenceIssue:
    token: str
    replacement: str
    classification: ReferenceClassification
    start: int
    end: int
    safe_auto_apply: bool


@dataclass(frozen=True)
class TextMetadataSource:
    test_id: str
    name: str
    database_name: str
    table_name: str
    key_columns: tuple[str, ...]
    text_columns: tuple[str, ...]
    active_column: str | None = "is_active"


DEFAULT_MIGRATION_RULES = (
    MigrationRule(
        old_token="v_relationship_paths",
        new_token="relationship_paths",
        classification=ReferenceClassification.STALE_ALIAS,
        safe_auto_apply=True,
    ),
    MigrationRule(
        old_token="v_relationship_patsh",
        new_token="relationship_paths",
        classification=ReferenceClassification.TYPO_SUSPECT,
        safe_auto_apply=True,
    ),
)


def find_text_reference_issues(
    text: str,
    migration_rules: tuple[MigrationRule, ...] = DEFAULT_MIGRATION_RULES,
) -> list[TextReferenceIssue]:
    issues: list[TextReferenceIssue] = []
    for rule in migration_rules:
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(rule.old_token)}(?![A-Za-z0-9_])", re.I)
        for match in pattern.finditer(text):
            issues.append(
                TextReferenceIssue(
                    token=match.group(0),
                    replacement=rule.new_token,
                    classification=rule.classification,
                    start=match.start(),
                    end=match.end(),
                    safe_auto_apply=rule.safe_auto_apply,
                )
            )
    return sorted(issues, key=lambda issue: issue.start)


def apply_safe_text_repairs(
    text: str,
    migration_rules: tuple[MigrationRule, ...] = DEFAULT_MIGRATION_RULES,
) -> str:
    repaired = text
    for rule in migration_rules:
        if not rule.safe_auto_apply:
            continue
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(rule.old_token)}(?![A-Za-z0-9_])", re.I)
        repaired = pattern.sub(rule.new_token, repaired)
    return repaired


def default_text_metadata_sources(prefix: str) -> list[TextMetadataSource]:
    sem_db = f"{prefix}_SEM_STD_V"
    mem_db = f"{prefix}_MEM_STD_V"
    return [
        TextMetadataSource(
            test_id=f"{prefix.upper()}-TEXT-001",
            name="Entity metadata free-text references are current",
            database_name=sem_db,
            table_name="entity_metadata",
            key_columns=("entity_metadata_id", "database_name", "table_name"),
            text_columns=("entity_name", "entity_description", "industry_standard"),
        ),
        TextMetadataSource(
            test_id=f"{prefix.upper()}-TEXT-002",
            name="Column metadata free-text references are current",
            database_name=sem_db,
            table_name="column_metadata",
            key_columns=("column_metadata_id", "database_name", "table_name", "column_name"),
            text_columns=("business_description", "data_type", "allowed_values_json"),
        ),
        TextMetadataSource(
            test_id=f"{prefix.upper()}-TEXT-003",
            name="Relationship metadata free-text references are current",
            database_name=sem_db,
            table_name="table_relationship",
            key_columns=("relationship_id", "relationship_name"),
            text_columns=("relationship_description", "relationship_meaning"),
        ),
        TextMetadataSource(
            test_id=f"{prefix.upper()}-TEXT-004",
            name="Query cookbook free-text references are current",
            database_name=mem_db,
            table_name="Query_Cookbook",
            key_columns=("recipe_id", "recipe_title"),
            text_columns=("recipe_description", "use_case", "performance_notes"),
        ),
        TextMetadataSource(
            test_id=f"{prefix.upper()}-TEXT-005",
            name="Business glossary free-text references are current",
            database_name=mem_db,
            table_name="Business_Glossary",
            key_columns=("term",),
            text_columns=("definition", "business_context", "related_table", "source_module"),
        ),
    ]


def run_text_reference_validation(adapter, source: TextMetadataSource) -> TestResult:
    test_case = text_reference_test_case(source)

    try:
        rows = adapter.fetch_all(test_case.sql)
    except Exception as exc:  # noqa: BLE001 - adapters normalise backend errors later.
        return TestResult(
            test_case=test_case,
            status=TestStatus.ERROR,
            row_count=0,
            error_message=str(exc),
        )

    findings = _find_source_issues(source, rows)
    return TestResult(
        test_case=test_case,
        status=TestStatus.PASSED if not findings else TestStatus.FAILED,
        row_count=len(findings),
        sample_rows=findings[:10],
    )


def run_text_reference_validations(
    prefix: str,
    adapter,
    sources: list[TextMetadataSource] | None = None,
) -> list[TestResult]:
    resolved_sources = sources or default_text_metadata_sources(prefix)
    return [run_text_reference_validation(adapter, source) for source in resolved_sources]


def text_reference_test_cases(prefix: str) -> list[TestCase]:
    return [text_reference_test_case(source) for source in default_text_metadata_sources(prefix)]


def text_reference_test_case(source: TextMetadataSource) -> TestCase:
    return TestCase(
        test_id=source.test_id,
        name=source.name,
        category=TestCategory.FREE_TEXT,
        severity=TestSeverity.WARNING,
        sql=_build_source_sql(source),
        expected_result="Returns zero stale free-text reference findings.",
        expected=ExpectedResult.ZERO_ROWS,
        repair_strategy="Apply safe migration rules or raise a repair proposal for steward approval.",
    )


def _build_source_sql(source: TextMetadataSource) -> str:
    selected_columns = []
    for column_name in (*source.key_columns, *source.text_columns):
        if column_name not in selected_columns:
            selected_columns.append(column_name)

    select_list = "\n   ,".join(selected_columns)
    sql = f"""
SELECT
    {select_list}
FROM {source.database_name}.{source.table_name}
""".strip()
    if source.active_column:
        sql += f"\nWHERE COALESCE({source.active_column}, 1) = 1"
    return sql


def _find_source_issues(
    source: TextMetadataSource,
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for row in rows:
        key_values = _key_values(source, row)
        row_key = _row_key(key_values)
        for column_name in source.text_columns:
            value = row.get(column_name)
            if value is None:
                continue
            for issue in find_text_reference_issues(str(value)):
                findings.append(
                    {
                        "database_name": source.database_name,
                        "table_name": source.table_name,
                        "column_name": column_name,
                        "row_key": row_key,
                        "key_values": key_values,
                        "token": issue.token,
                        "replacement": issue.replacement,
                        "classification": issue.classification.value,
                        "safe_auto_apply": issue.safe_auto_apply,
                    }
                )
    return findings


def _key_values(source: TextMetadataSource, row: dict[str, object]) -> dict[str, object]:
    return {column_name: row[column_name] for column_name in source.key_columns if row.get(column_name) is not None}


def _row_key(key_values: dict[str, object]) -> str:
    parts = []
    for column_name, value in key_values.items():
        parts.append(f"{column_name}={value}")
    return "; ".join(parts)
