"""Rule enablement configuration for validation runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ai_native_data_product_trust_engine.models import TestCase


SCANNER_IDS = {
    "CAPABILITY": "include_capability_scans",
    "QUERY": "include_query_template_scans",
    "RELATIONSHIP": "include_relationship_health_scans",
    "TEXT": "include_text_reference_scans",
    "VIEW": "include_view_contract_scans",
}


@dataclass(frozen=True)
class RuleConfig:
    disabled_test_ids: frozenset[str] = field(default_factory=frozenset)
    disabled_scanners: frozenset[str] = field(default_factory=frozenset)

    def filter_tests(self, tests: Iterable[TestCase]) -> list[TestCase]:
        return [test for test in tests if test.test_id.upper() not in self.disabled_test_ids]

    def scanner_kwargs(self) -> dict[str, bool]:
        return {
            kwarg_name: scanner_id not in self.disabled_scanners
            for scanner_id, kwarg_name in SCANNER_IDS.items()
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
