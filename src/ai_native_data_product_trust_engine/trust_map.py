"""Per-area trust map (design standard: validation pattern, wire schema 2.1).

Trust is published per *area* (module, entity, pattern, capability or product)
rather than as one product-level gate. This module does two things:

* :func:`scope_for_check` resolves the area a check belongs to. By the
  standard's ownership rule, a check's scope is the module or pattern that
  owns it, so the existing check suite acquires its scope without being
  rewritten.
* :func:`build_trust_map` rolls a run's results up into one :class:`AreaEntry`
  per area the profile covers - including areas that have no checks, which are
  published as ``no-evidence`` rather than left out (VAL-18).

Only the validator computes trust; consumers read these entries and never
re-derive them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ai_native_data_product_trust_engine.models import (
    ExcludedCheck,
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)

SCOPE_KINDS = ("MODULE", "ENTITY", "PATTERN", "CAPABILITY", "PRODUCT")

# Check-family token(s) after the product prefix -> owning area. Longest match
# wins, so ``QUERY-EXPLAIN-<recipe>`` resolves through ``QUERY``. A family not
# listed here belongs to the product as a whole.
_FAMILY_SCOPES: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (("SEM",), ("MODULE", "semantic")),
    (("LAYOUT",), ("MODULE", "semantic")),
    (("DISCOVERY",), ("MODULE", "semantic")),
    (("REL",), ("MODULE", "semantic")),
    (("RELATIONSHIP",), ("MODULE", "semantic")),
    (("TEXT",), ("MODULE", "semantic")),
    (("OPS",), ("MODULE", "observability")),
    (("QUERY",), ("MODULE", "memory")),
    (("PERF",), ("MODULE", "memory")),
    (("TEMPORAL",), ("PATTERN", "temporal-lifecycle-metadata")),
    (("STD", "VIEW"), ("PATTERN", "object-placement")),
    (("STD", "TABLE", "VIEW"), ("PATTERN", "object-placement")),
    (("BUS", "VIEW"), ("PATTERN", "object-placement")),
    (("VIEW",), ("PATTERN", "object-placement")),
    (("STRUCT",), ("PRODUCT", "")),
    (("CAP",), ("PRODUCT", "")),
    (("CAPABILITY",), ("PRODUCT", "")),
)


@dataclass(frozen=True)
class AreaEntry:
    """One ``validation_area`` row: the trust picture for a single area."""

    scope_kind: str
    scope_id: str
    checks_expected: int
    checks_ran: int
    passed_count: int
    failed_count: int
    error_count: int
    critical_failure_count: int
    error_failure_count: int
    area_status: str
    confidence: str
    open_gaps: str | None
    recommended_action: str | None


def scope_for_check(check_id: str, prefix: str) -> tuple[str, str]:
    """Return ``(scope_kind, scope_id)`` for a check id such as ``EXAMPLEPRODUCT-SEM-008``.

    The family is the dash-separated token run after the product prefix. A check
    whose family is not catalogued falls back to the ``PRODUCT`` area, which is
    the honest answer for "belongs to no narrower area".
    """
    tokens = _family_tokens(check_id, prefix)
    best: tuple[str, str] | None = None
    best_len = 0
    for family, scope in _FAMILY_SCOPES:
        if len(family) > best_len and tokens[: len(family)] == family:
            best, best_len = scope, len(family)
    if best is None:
        return "PRODUCT", prefix
    kind, scope_id = best
    return kind, scope_id or prefix


def scope_for_result(result: TestResult, prefix: str) -> tuple[str, str]:
    return scope_for_check(result.test_case.test_id, prefix)


def build_trust_map(
    run: ValidationRun,
    declared_modules: Iterable[str] = (),
) -> list[AreaEntry]:
    """Roll a run up into one entry per area its profile covers.

    ``declared_modules`` are modules the product declares as deployed (from its
    ``data_product_map``). Any with no checks are published as ``no-evidence`` so
    an unchecked module is visible instead of reading as clean.
    """
    prefix = run.prefix
    results_by_area: dict[tuple[str, str], list[TestResult]] = {}
    excluded_by_area: dict[tuple[str, str], list[ExcludedCheck]] = {}
    for result in run.results:
        results_by_area.setdefault(scope_for_result(result, prefix), []).append(result)
    for excluded in run.excluded_checks:
        excluded_by_area.setdefault(scope_for_check(excluded.check_id, prefix), []).append(excluded)

    areas = set(results_by_area) | set(excluded_by_area)
    for module in declared_modules:
        module_id = str(module).strip().lower()
        if module_id:
            areas.add(("MODULE", module_id))

    entries = [
        _entry(kind, scope_id, results_by_area.get((kind, scope_id), []), excluded_by_area.get((kind, scope_id), []))
        for kind, scope_id in sorted(areas)
    ]
    return entries


def _entry(
    kind: str,
    scope_id: str,
    results: list[TestResult],
    excluded: list[ExcludedCheck],
) -> AreaEntry:
    ran = len(results)
    # A layout exclusion is reported but is not an expected check (INV-LAYOUT-007).
    counted_exclusions = [check for check in excluded if check.counts_as_expected]
    expected = ran + len(counted_exclusions)
    passed = sum(1 for r in results if r.status == TestStatus.PASSED)
    failed = sum(1 for r in results if r.status == TestStatus.FAILED)
    errored = sum(1 for r in results if r.status == TestStatus.ERROR)
    bad = [r for r in results if r.status in {TestStatus.FAILED, TestStatus.ERROR}]
    critical = sum(1 for r in bad if r.test_case.severity == TestSeverity.CRITICAL)
    error_sev = sum(1 for r in bad if r.test_case.severity == TestSeverity.ERROR)

    status = _area_status(expected, ran, failed + errored)
    confidence = _confidence(expected, ran, failed + errored, critical + error_sev)
    gaps, action = _guidance(
        kind, scope_id, confidence, results, counted_exclusions, expected, ran
    )
    return AreaEntry(
        scope_kind=kind,
        scope_id=scope_id,
        checks_expected=expected,
        checks_ran=ran,
        passed_count=passed,
        failed_count=failed,
        error_count=errored,
        critical_failure_count=critical,
        error_failure_count=error_sev,
        area_status=status,
        confidence=confidence,
        open_gaps=gaps,
        recommended_action=action,
    )


def _area_status(expected: int, ran: int, failures: int) -> str:
    if expected == 0:
        return "no-evidence"
    if ran == 0:
        return "not-validated"
    if failures:
        return "fail"
    return "pass" if ran == expected else "partial"


def _confidence(expected: int, ran: int, failures: int, severe: int) -> str:
    # Rules apply in order, first match wins (standard section 4.3).
    if ran == 0:
        return "unknown"
    if severe or ran * 2 < expected:
        return "weak"
    if failures or ran < expected:
        return "partial"
    return "strong"


def _guidance(
    kind: str,
    scope_id: str,
    confidence: str,
    results: list[TestResult],
    excluded: list[ExcludedCheck],
    expected: int,
    ran: int,
) -> tuple[str | None, str | None]:
    """open_gaps / recommended_action; required below ``strong`` (VAL-17)."""
    if confidence == "strong":
        return None, None
    if expected == 0:
        return (
            f"No checks are defined for {kind.lower()} '{scope_id}', so nothing is known about it.",
            f"Write conformance checks for {kind.lower()} '{scope_id}' and add them to the validator profile.",
        )
    if ran == 0:
        return (
            f"{expected} check(s) are defined for '{scope_id}' but none ran in this run.",
            "Re-run the validator with this area's scanners enabled.",
        )
    gaps: list[str] = []
    actions: list[str] = []
    bad = [r for r in results if r.status in {TestStatus.FAILED, TestStatus.ERROR}]
    if bad:
        worst = sorted(bad, key=lambda r: _SEVERITY_ORDER[r.test_case.severity])
        names = "; ".join(r.test_case.name for r in worst[:3])
        more = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
        gaps.append(f"{len(bad)} failing check(s): {names}{more}.")
        strategies = [r.test_case.repair_strategy for r in worst if r.test_case.repair_strategy]
        actions.append(strategies[0] if strategies else "Resolve the failing checks and re-run the validator.")
    if excluded:
        gaps.append(f"{len(excluded)} check(s) excluded by configuration.")
        actions.append("Review the excluded checks and re-enable any that should apply.")
    if ran < expected and not excluded:
        gaps.append(f"Only {ran} of {expected} defined check(s) ran.")
        actions.append("Re-run the validator with every scanner enabled.")
    return " ".join(gaps)[:1000], " ".join(actions)[:1000]


_SEVERITY_ORDER = {
    TestSeverity.CRITICAL: 0,
    TestSeverity.ERROR: 1,
    TestSeverity.WARNING: 2,
    TestSeverity.INFO: 3,
}


def _family_tokens(check_id: str, prefix: str) -> tuple[str, ...]:
    text = check_id.upper()
    lead = f"{prefix.upper()}-"
    if text.startswith(lead):
        text = text[len(lead):]
    # Per-object ids append "-<db>.<view>" or "-<slug>"; the family tokens are all
    # that matter, and a longest-prefix match ignores whatever trails them.
    return tuple(text.split("-"))
