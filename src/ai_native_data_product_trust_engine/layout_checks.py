"""Checks and exclusions that come from the resolved layout.

* ``{PREFIX}-LAYOUT-001`` reports ``LAYOUT_NOT_DECLARED`` when the product did not
  declare a value the run needed (Platform Layout Standard section 6, VAL-20). It
  is a metadata gap, not a design failure, hence WARNING severity.
* Checks that depend on the ACCESS layer are excluded, with a reason, when the
  declared layout has no ACCESS container (section 7, rules 2 and 3, VAL-21).
"""

from __future__ import annotations

from ai_native_data_product_trust_engine.layout import (
    ACCESS_DEPENDENT_CHECKS,
    ISSUE_LAYOUT_NOT_DECLARED,
    PLATFORM_TERADATA,
    Layout,
    access_layer_exclusion_reason,
    sql_string,
)
from ai_native_data_product_trust_engine.models import (
    ExcludedCheck,
    TestCase,
    TestCategory,
    TestSeverity,
)

LAYOUT_CHECK_SUFFIX = "LAYOUT-001"

_COLUMNS = (
    ("product_id", 128),
    ("value_name", 64),
    ("resolved_value", 512),
    ("resolved_source", 32),
    ("issue_code", 64),
    ("issue_detail", 1000),
    ("repair_hint", 1000),
    ("registry_database", 128),
    ("registry_view", 128),
    ("inferred_platform_profile", 64),
    ("inferred_standard_version", 64),
)

_VALUE_DETAIL = {
    "platform_profile": "The registry row does not record platform_profile.",
    "standard_version": "The registry row does not record standard_version.",
    "layer_bindings": (
        "No layer bindings were found (governance.data_product_container or access_object), "
        "so object roles were inferred from the legacy _STD_T / _STD_V / _BUS_V suffixes."
    ),
    "semantic_database": "The Semantic database was inferred from the product prefix.",
    "observability_database": "The Observability database was inferred from the product prefix.",
    "memory_database": "The Memory database was inferred from the product prefix.",
}


def layout_check_id(prefix: str) -> str:
    return f"{prefix.upper()}-{LAYOUT_CHECK_SUFFIX}"


def layout_test_cases(prefix: str, layout: Layout | None = None) -> list[TestCase]:
    """The layout check. Kept out of ``generate_metadata_tests`` so that list is stable."""
    from ai_native_data_product_trust_engine.layout import derive_layout

    resolved = layout or derive_layout(prefix)
    return [
        TestCase(
            test_id=layout_check_id(prefix),
            name="Product layout is declared in its Semantic metadata",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.WARNING,
            sql=_layout_sql(prefix, resolved),
            expected_result=(
                "Returns zero rows when the product declares its platform, standard version and "
                "layer bindings, so no physical name has to be inferred."
            ),
            repair_strategy=(
                "Record platform_profile and standard_version on the registry row and the layer "
                "bindings in governance.data_product_container / access_object. Until then the "
                "engine resolves names by the legacy convention (Platform Layout Standard "
                "section 6, priority 4)."
            ),
            inspection_scope=(
                f"{resolved.registry_database}.{resolved.registry_view} platform_profile and "
                "standard_version, governance.data_product_container and access_object"
            ),
        )
    ]


def layout_excluded_checks(prefix: str, layout: Layout | None) -> list[ExcludedCheck]:
    """Checks that do not apply because the declared layout lacks an ACCESS layer."""
    if layout is None or layout.access_layer_declared:
        return []
    reason = access_layer_exclusion_reason(layout)
    return [
        ExcludedCheck(
            check_id=f"{prefix.upper()}-{suffix}",
            name=name,
            category=TestCategory.STRUCTURAL.value,
            reason=reason,
            counts_as_expected=False,
        )
        for suffix, name in ACCESS_DEPENDENT_CHECKS
    ]


def _layout_sql(prefix: str, layout: Layout) -> str:
    undeclared = layout.undeclared_values()
    cast_columns = ", ".join(f"CAST(NULL AS VARCHAR({size})) AS {name}" for name, size in _COLUMNS)
    if not undeclared:
        return f"SELECT {cast_columns}\nWHERE 1 = 0;"
    profile = layout.platform_profile or PLATFORM_TERADATA
    version = layout.standard_version
    selects = []
    for index, name in enumerate(undeclared):
        values = {
            "product_id": prefix,
            "value_name": name,
            "resolved_value": _resolved_value(layout, name),
            "resolved_source": layout.source_of(name),
            "issue_code": ISSUE_LAYOUT_NOT_DECLARED,
            "issue_detail": _VALUE_DETAIL.get(name, f"{name} was not declared by the product."),
            "repair_hint": (
                "Record the product layout declaration (see the repair candidate) so readers "
                "resolve names from the product instead of inferring them."
            ),
            "registry_database": layout.registry_database,
            "registry_view": layout.registry_view,
            "inferred_platform_profile": profile,
            "inferred_standard_version": version,
        }
        rendered = []
        for column, size in _COLUMNS:
            value = values[column]
            literal = "NULL" if value is None else sql_string(value)
            if index == 0:
                literal = f"CAST({literal} AS VARCHAR({size}))"
            rendered.append(f"{literal} AS {column}" if index == 0 else literal)
        selects.append(
            "SELECT\n    "
            + "\n   ,".join(rendered)
            + "\nFROM DBC.DBCInfoV WHERE InfoKey = 'VERSION'"
        )
    return "\nUNION ALL\n".join(selects) + "\nORDER BY 2;"


def _resolved_value(layout: Layout, name: str) -> str | None:
    if name == "layer_bindings":
        return "legacy suffix derivation (_STD_T storage, _STD_V access, _BUS_V consumer)"
    if name in {"platform_profile", "standard_version"}:
        return getattr(layout, name)
    return getattr(layout, name, None)
