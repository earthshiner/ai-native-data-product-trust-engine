"""Rule enablement configuration for validation runs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ai_native_data_product_trust_engine.models import ExcludedCheck, TestCase

SCANNER_IDS = {
    "CAPABILITY": {
        "kwarg": "include_capability_scans",
        "name": "Capability discovery scans",
        "category": "CAPABILITY",
        "description": (
            "Checks whether metadata and recipes only claim platform features that the deployed "
            "product actually exposes."
        ),
    },
    "QUERY": {
        "kwarg": "include_query_template_scans",
        "name": "Query template scans",
        "category": "QUERY",
        "description": (
            "Validates active cookbook SQL templates, bounded-query safeguards, parameters and "
            "EXPLAIN readiness."
        ),
    },
    "RELATIONSHIP": {
        "kwarg": "include_relationship_health_scans",
        "name": "Relationship health scans",
        "category": "DATA_QUALITY",
        "description": (
            "Samples declared relationship keys for orphan evidence, cardinality mismatches and "
            "temporal current-record contract issues."
        ),
    },
    "TEXT": {
        "kwarg": "include_text_reference_scans",
        "name": "Free-text reference scans",
        "category": "FREE_TEXT",
        "description": (
            "Checks glossary text, cookbook notes and metadata descriptions for stale object "
            "names, aliases and free-text references."
        ),
    },
    "VIEW": {
        "kwarg": "include_view_contract_scans",
        "name": "View contract scans",
        "category": "STRUCTURAL",
        "description": (
            "Validates standard view contracts, business-view source layering, locking access "
            "patterns and view compile/readiness checks."
        ),
    },
}


@dataclass(frozen=True)
class RuleConfig:
    disabled_test_ids: frozenset[str] = field(default_factory=frozenset)
    disabled_scanners: frozenset[str] = field(default_factory=frozenset)

    def filter_tests(self, tests: Iterable[TestCase]) -> list[TestCase]:
        return [test for test in tests if test.test_id.upper() not in self.disabled_test_ids]

    def excluded_checks(self, tests: Iterable[TestCase]) -> list[ExcludedCheck]:
        generated_tests = list(tests)
        generated_by_id = {test.test_id.upper(): test for test in generated_tests}
        excluded = [
            ExcludedCheck(
                check_id=test.test_id,
                name=test.name,
                category=test.category.value,
                reason="Disabled by disabled_test_ids rule configuration.",
            )
            for test in generated_tests
            if test.test_id.upper() in self.disabled_test_ids
        ]
        for test_id in sorted(self.disabled_test_ids - set(generated_by_id)):
            excluded.append(
                ExcludedCheck(
                    check_id=test_id,
                    name="Configured check id was not generated",
                    category="UNKNOWN",
                    reason="Configured in disabled_test_ids but no generated check matched.",
                )
            )
        for scanner_id in sorted(self.disabled_scanners):
            scanner = SCANNER_IDS[scanner_id]
            excluded.append(
                ExcludedCheck(
                    check_id=f"SCANNER:{scanner_id}",
                    name=str(scanner["name"]),
                    category=str(scanner["category"]),
                    reason=(
                        f"Disabled by disabled_scanners rule configuration. "
                        f"{scanner['description']}"
                    ),
                )
            )
        return excluded

    def scanner_kwargs(self) -> dict[str, bool]:
        return {
            str(scanner["kwarg"]): scanner_id not in self.disabled_scanners
            for scanner_id, scanner in SCANNER_IDS.items()
        }


def load_rule_config(path: Path | None) -> RuleConfig:
    if path is None:
        return RuleConfig()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = (
            f"[ADPTrust.InvalidRuleConfig] Could not parse rule config {path}. "
            f"Suggested action: fix the JSON syntax near line {exc.lineno}, column {exc.colno}."
        )
        raise ValueError(msg) from exc

    disabled_test_ids = _normalised_set(payload.get("disabled_test_ids", ()))
    disabled_scanners = _normalised_set(payload.get("disabled_scanners", ()))
    unknown_scanners = disabled_scanners - set(SCANNER_IDS)
    if unknown_scanners:
        scanner_list = ", ".join(sorted(unknown_scanners))
        msg = (
            f"[ADPTrust.InvalidRuleConfig] Unknown disabled_scanners value: {scanner_list}. "
            f"Suggested action: use one of {', '.join(sorted(SCANNER_IDS))}."
        )
        raise ValueError(msg)

    return RuleConfig(
        disabled_test_ids=frozenset(disabled_test_ids),
        disabled_scanners=frozenset(disabled_scanners),
    )


def _normalised_set(value: object) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list):
        msg = (
            "[ADPTrust.InvalidRuleConfig] Rule config values must be arrays. "
            "Suggested action: set disabled_test_ids and disabled_scanners to JSON arrays."
        )
        raise ValueError(msg)
    return {str(item).strip().upper() for item in value if str(item).strip()}
