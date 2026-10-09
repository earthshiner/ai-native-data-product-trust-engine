"""Checks and exclusions that come from the resolved layout.

* ``{PREFIX}-LAYOUT-001`` reports ``LAYOUT_NOT_DECLARED`` when the product did not
  declare a value the run needed (Platform Layout Standard section 6, VAL-20). It
  is a metadata gap, not a design failure, hence WARNING severity.
* Checks that depend on the ACCESS layer are excluded, with a reason, when the
  declared layout has no ACCESS container (section 7, rules 2 and 3, VAL-21).

LAYOUT-001 is evaluated from the resolved layout rather than by a database query:
the evidence is how the names were resolved, which only the engine knows.
"""

from __future__ import annotations

from ai_native_data_product_trust_engine.layout import (
    ACCESS_DEPENDENT_CHECKS,
    ISSUE_LAYOUT_NOT_DECLARED,
    PLATFORM_TERADATA,
    Layout,
    access_layer_exclusion_reason,
    derive_layout,
)
from ai_native_data_product_trust_engine.models import (
    ExcludedCheck,
    ExpectedResult,
    TestCase,
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
)

LAYOUT_CHECK_SUFFIX = "LAYOUT-001"

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

_REPAIR_HINT = (
    "Record the product layout declaration (see the repair candidate) so readers resolve "
    "names from the product instead of inferring them."
)


def layout_check_id(prefix: str) -> str:
    return f"{prefix.upper()}-{LAYOUT_CHECK_SUFFIX}"


def layout_test_cases(prefix: str, layout: Layout | None = None) -> list[TestCase]:
    """The layout check. Kept out of ``generate_metadata_tests`` so that list is stable."""
    resolved = layout or derive_layout(prefix)
    return [
        TestCase(
            test_id=layout_check_id(prefix),
            name="Product layout is declared in its Semantic metadata",
            category=TestCategory.SEMANTIC,
            severity=TestSeverity.WARNING,
            sql="-- Evaluated from the resolved layout; no database query is issued.",
            expected_result=(
                "Passes when the product declares its platform, standard version and layer "
                "bindings, so no physical name has to be inferred."
            ),
            expected=ExpectedResult.ZERO_ROWS,
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


def layout_check_result(prefix: str, layout: Layout) -> TestResult:
    """LAYOUT-001: one evidence row per value the product left for the engine to infer."""
    case = layout_test_cases(prefix, layout)[0]
    undeclared = layout.undeclared_values()
    if not undeclared:
        return TestResult(test_case=case, status=TestStatus.PASSED, row_count=0)
    rows = [
        {
            "product_id": prefix,
            "value_name": name,
            "resolved_value": _resolved_value(layout, name),
            "resolved_source": layout.source_of(name),
            "issue_code": ISSUE_LAYOUT_NOT_DECLARED,
            "issue_detail": _VALUE_DETAIL.get(name, f"{name} was not declared by the product."),
            "repair_hint": _REPAIR_HINT,
            "registry_database": layout.registry_database,
            "registry_view": layout.registry_view,
            "inferred_platform_profile": layout.platform_profile or PLATFORM_TERADATA,
            "inferred_standard_version": layout.standard_version,
        }
        for name in undeclared
    ]
    return TestResult(
        test_case=case,
        status=TestStatus.FAILED,
        row_count=len(rows),
        sample_rows=rows[:10],
    )


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


def _resolved_value(layout: Layout, name: str) -> str | None:
    if name == "layer_bindings":
        return "legacy suffix derivation (_STD_T storage, _STD_V access, _BUS_V consumer)"
    value = getattr(layout, name, None)
    return None if value is None else str(value)
