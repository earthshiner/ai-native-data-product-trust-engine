"""Detect object-like references inside free-text metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


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
