"""Rule enablement configuration for validation runs."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ai_native_data_product_trust_engine.layout import LAYER_ROLES, LayoutOverrides
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
    # Two-part publish target for the trust summary row. Sets WHERE a
    # valueless --publish-trust-table writes; publishing still requires the
    # CLI flag, and an explicit CLI value overrides this.
    publish_trust_table: str | None = None
    # Single-database target for the standard validation results (validation_run
    # and validation_area). Sets WHERE a valueless --publish-validation writes.
    publish_validation_database: str | None = None
    # Priority-2 layout names (Platform Layout Standard section 6). Local to this
    # evaluator: they are never written into the product.
    layout: LayoutOverrides | None = None

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

    disabled_test_ids = _normalised_set(payload.get("disabled_test_ids"))
    disabled_scanners = _normalised_set(payload.get("disabled_scanners"))
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
        publish_trust_table=_publish_trust_table(payload.get("publish_trust_table")),
        publish_validation_database=_publish_validation_database(
            payload.get("publish_validation_database")
        ),
        layout=_layout_overrides(payload.get("layout")),
    )


_LAYOUT_NAME_KEYS = (
    "semantic_database",
    "observability_database",
    "memory_database",
    "registry_database",
    "registry_view",
    "graph_catalogue_database",
    "governance_database",
)
_LAYOUT_KEYS = (*_LAYOUT_NAME_KEYS, "layer_code_map")
_LAYOUT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$#]*")


def _layout_overrides(value: object) -> LayoutOverrides | None:
    """Validate the optional ``layout`` object: container names and a layer code map."""
    if value is None:
        return None
    if not isinstance(value, dict):
        msg = (
            "[ADPTrust.InvalidRuleConfig] layout must be an object. "
            f"Suggested action: use keys from {', '.join(_LAYOUT_KEYS)}."
        )
        raise ValueError(msg)
    unknown = sorted(set(value) - set(_LAYOUT_KEYS))
    if unknown:
        msg = (
            f"[ADPTrust.InvalidRuleConfig] Unknown layout key: {', '.join(unknown)}. "
            f"Suggested action: use keys from {', '.join(_LAYOUT_KEYS)}."
        )
        raise ValueError(msg)
    names: dict[str, str | None] = {}
    for key in _LAYOUT_NAME_KEYS:
        raw = value.get(key)
        name = str(raw).strip() if raw is not None else ""
        if name and not _LAYOUT_NAME.fullmatch(name):
            msg = (
                f"[ADPTrust.InvalidRuleConfig] layout.{key} must be a single Teradata "
                f"object name, got: {name!r}. "
                "Suggested action: set it like 'ProductPrefix_SEM_ACL_V'."
            )
            raise ValueError(msg)
        names[key] = name or None
    layer_code_map = _layer_code_map(value.get("layer_code_map"))
    overrides = LayoutOverrides(layer_code_map=layer_code_map, **names)
    return None if overrides.is_empty() else overrides


def _layer_code_map(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        msg = (
            "[ADPTrust.InvalidRuleConfig] layout.layer_code_map must map layer codes to "
            f"layer roles. Suggested action: use roles from {', '.join(LAYER_ROLES)}."
        )
        raise ValueError(msg)
    mapped: dict[str, str] = {}
    for code, role in value.items():
        role_name = str(role).strip().upper()
        if not str(code).strip() or role_name not in LAYER_ROLES:
            msg = (
                f"[ADPTrust.InvalidRuleConfig] layout.layer_code_map entry {code!r}: {role!r} "
                f"is not a layer role. Suggested action: use one of {', '.join(LAYER_ROLES)}."
            )
            raise ValueError(msg)
        mapped[str(code).strip().upper()] = role_name
    return mapped or None


def _publish_validation_database(value: object) -> str | None:
    """Validate the optional validation publish target: one database name."""
    if value is None:
        return None
    target = str(value).strip()
    if not target:
        return None
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", target):
        msg = (
            f"[ADPTrust.InvalidRuleConfig] publish_validation_database must be a single "
            f"Teradata database name, got: {target!r}. "
            "Suggested action: set it like 'ProductPrefix_OBS_STD_T'."
        )
        raise ValueError(msg)
    return target


def _publish_trust_table(value: object) -> str | None:
    """Validate the optional publish target: a two-part database.table name."""
    if value is None:
        return None
    target = str(value).strip()
    if not target:
        return None
    parts = target.split(".")
    if len(parts) != 2 or not all(part.strip() for part in parts):
        msg = (
            f"[ADPTrust.InvalidRuleConfig] publish_trust_table must be a two-part "
            f"Teradata table name (database.table), got: {target!r}. "
            "Suggested action: set it like 'ProductPrefix_OBS_STD_T.trust_engine_run'."
        )
        raise ValueError(msg)
    return target


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
