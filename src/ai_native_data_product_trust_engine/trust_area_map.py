"""Publish the per-area trust map (the trust heatmap) for a validation run.

The run record in ``trust_engine_run`` summarises a whole run. The trust
area map resolves the same evidence per area, so an agent or a reviewer
can see which parts of a data product are proven, which are weak, and
which no check has reached. It is knowledge, not a gate: no value here
withholds use of the product. The Data Product Browser renders it as the
trust heatmap from ``{prefix}_OBS_ACL_V.trust_area_map``.

Areas
    Every check belongs to one area, assigned by the Trust Engine's own
    check-family profile (``AREA_PROFILE``), never by a data object's name:

        module  Semantic       catalogue, discovery and free-text checks
        module  Observability  operational evidence and lineage endpoints
        module  Memory         Query_Cookbook checks
        module  Domain         relationship health and temporal contracts
        pattern physical-design       storage, primary index, statistics
        pattern view-contracts        view contract scans
        capability capability-claims  platform capability claims

    Every module the product registers as deployed in
    ``data_product_map`` gets an entry even when no check covers it, so an
    unvalidated module reads as ``no-evidence``, never as sound.

Status and confidence follow the AI-Native validation pattern (§4.3):
coverage is checks ran over checks expected, where checks disabled by
rule configuration count as expected but not run.

Publishing replaces the product's map in one multi-statement request
(``DELETE`` then one ``INSERT`` per area), so readers never see a mix of
two runs. The run history stays in ``trust_engine_run``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ai_native_data_product_trust_engine.models import (
    ExcludedCheck,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.trust_publish import (
    PublishAdapter,
    _qualified_identifier,
    _sql_literal,
    _timestamp_literal,
)

LOGGER = logging.getLogger(__name__)

MODULE = "module"
PATTERN = "pattern"
CAPABILITY = "capability"

STANDARD_MODULES = ("Domain", "Semantic", "Search", "Prediction", "Observability", "Memory")

#: Check family (the token after ``{PREFIX}-`` in a test id) to its area.
AREA_PROFILE: dict[str, tuple[str, str]] = {
    "SEM": (MODULE, "Semantic"),
    "DISCOVERY": (MODULE, "Semantic"),
    "TEXT": (MODULE, "Semantic"),
    "OPS": (MODULE, "Observability"),
    "QUERY": (MODULE, "Memory"),
    "REL": (MODULE, "Domain"),
    "TEMPORAL": (MODULE, "Domain"),
    "STRUCT": (PATTERN, "physical-design"),
    "PERF": (PATTERN, "physical-design"),
    "VIEW": (PATTERN, "view-contracts"),
    "STD": (PATTERN, "view-contracts"),  # STD-VIEW-*, STD-TABLE-VIEW-COVERAGE
    "BUS": (PATTERN, "view-contracts"),  # BUS-VIEW-SOURCES
    "CAP": (CAPABILITY, "capability-claims"),
    # Scanner summary checks (e.g. CAPABILITY-SCAN, RELATIONSHIP-SCAN).
    "CAPABILITY": (CAPABILITY, "capability-claims"),
    "RELATIONSHIP": (MODULE, "Domain"),
}

#: Individual checks whose area differs from their family's.
AREA_OVERRIDES: dict[str, tuple[str, str]] = {
    "SEM-011": (MODULE, "Observability"),  # lineage endpoints live in Observability
    "TEXT-004": (MODULE, "Memory"),  # Query_Cookbook free text
}

#: Disabled scanner families (``SCANNER:<id>`` exclusions) to their area.
SCANNER_AREAS: dict[str, tuple[str, str]] = {
    "CAPABILITY": (CAPABILITY, "capability-claims"),
    "QUERY": (MODULE, "Memory"),
    "RELATIONSHIP": (MODULE, "Domain"),
    "TEXT": (MODULE, "Semantic"),
    "VIEW": (PATTERN, "view-contracts"),
}

UNASSIGNED_AREA = (PATTERN, "unassigned-checks")

_TEXT_LIMIT = 1000
_LISTED = 3


def default_trust_area_table(prefix: str) -> str:
    return f"{prefix}_OBS_STD_T.trust_area_map"


def trust_area_map_ddl(prefix: str, table_name: str | None = None) -> str:
    """The trust area map table, matching the published ANDP example."""
    qualified_table = _qualified_identifier(table_name or default_trust_area_table(prefix))
    return f"""CREATE MULTISET TABLE {qualified_table}
(
    trust_area_id INTEGER NOT NULL GENERATED ALWAYS AS IDENTITY
        (START WITH 1 INCREMENT BY 1 NO CYCLE),
    scope_type VARCHAR(20) CHARACTER SET LATIN NOT CASESPECIFIC NOT NULL,
    scope_name VARCHAR(100) CHARACTER SET LATIN NOT CASESPECIFIC NOT NULL,
    coverage DECIMAL(5,4),
    status VARCHAR(20) CHARACTER SET LATIN NOT CASESPECIFIC,
    confidence VARCHAR(20) CHARACTER SET LATIN NOT CASESPECIFIC,
    gaps VARCHAR(1000) CHARACTER SET LATIN NOT CASESPECIFIC,
    recommendation VARCHAR(1000) CHARACTER SET LATIN NOT CASESPECIFIC,
    measured_dts TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6)
)
PRIMARY INDEX (trust_area_id);"""


# ---------------------------------------------------------------------------
# Area assignment
# ---------------------------------------------------------------------------


def area_for_check(check_id: str, prefix: str) -> tuple[str, str]:
    """The area a check belongs to, from the check-family profile."""
    upper_id = check_id.upper()
    if upper_id.startswith("SCANNER:"):
        return SCANNER_AREAS.get(upper_id.split(":", 1)[1], UNASSIGNED_AREA)
    stem = upper_id[len(prefix) + 1 :] if upper_id.startswith(f"{prefix.upper()}-") else upper_id
    for override, area in AREA_OVERRIDES.items():
        if stem == override or stem.startswith(f"{override}-"):
            return area
    return AREA_PROFILE.get(stem.split("-", 1)[0], UNASSIGNED_AREA)


# ---------------------------------------------------------------------------
# Area evaluation
# ---------------------------------------------------------------------------


@dataclass
class TrustArea:
    """One cell of the trust heatmap."""

    scope_type: str
    scope_name: str
    checks_expected: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    blocking_failures: int = 0  # CRITICAL or ERROR severity among failed/errored
    failed_names: list[str] = field(default_factory=list)
    repair_strategies: list[str] = field(default_factory=list)
    disabled_names: list[str] = field(default_factory=list)

    @property
    def checks_ran(self) -> int:
        return self.passed + self.failed + self.errors

    @property
    def coverage(self) -> float | None:
        if self.checks_expected == 0:
            return None
        return round(self.checks_ran / self.checks_expected, 4)

    @property
    def status(self) -> str:
        if self.checks_expected == 0:
            return "no-evidence"
        if self.checks_ran == 0:
            return "not-validated"
        if self.failed or self.errors:
            return "fail"
        return "pass" if self.checks_ran == self.checks_expected else "partial"

    @property
    def confidence(self) -> str:
        if self.checks_ran == 0:
            return "unknown"
        coverage = self.coverage or 0.0
        if self.blocking_failures or self.errors or coverage < 0.5:
            return "weak"
        if self.failed or self.checks_ran < self.checks_expected:
            return "partial"
        return "strong"

    @property
    def gaps(self) -> str | None:
        if self.confidence == "strong":
            return None
        parts = []
        if self.checks_expected == 0:
            parts.append(f"No Trust Engine check covers the {self.scope_name} {self.scope_type}.")
        if self.failed or self.errors:
            outcome = []
            if self.failed:
                outcome.append(f"{self.failed} failed")
            if self.errors:
                outcome.append(f"{self.errors} could not run")
            parts.append(
                f"{' and '.join(outcome)} of {self.checks_ran} checks: "
                f"{_listed(self.failed_names)}."
            )
        if self.disabled_names:
            parts.append(
                f"{len(self.disabled_names)} of {self.checks_expected} checks disabled by rule "
                f"configuration: {_listed(self.disabled_names)}."
            )
        return _clip(" ".join(parts))

    @property
    def recommendation(self) -> str | None:
        if self.confidence == "strong":
            return None
        if self.repair_strategies:
            return _clip(self.repair_strategies[0])
        if self.disabled_names:
            return _clip("Re-enable the disabled checks, or record why this area is out of scope.")
        return _clip(
            f"Add Trust Engine checks for the {self.scope_name} {self.scope_type} so its trust is "
            "evidenced rather than unknown."
        )


def build_trust_areas(
    run: ValidationRun,
    deployed_modules: list[str] | None = None,
) -> list[TrustArea]:
    """Resolve a run into one trust area per covered or deployed area."""
    areas: dict[tuple[str, str], TrustArea] = {}

    def area(key: tuple[str, str]) -> TrustArea:
        if key not in areas:
            areas[key] = TrustArea(scope_type=key[0], scope_name=key[1])
        return areas[key]

    for module in deployed_modules or []:
        area((MODULE, module))
    for result in run.results:
        entry = area(area_for_check(result.test_case.test_id, run.prefix))
        entry.checks_expected += 1
        if result.status == TestStatus.PASSED:
            entry.passed += 1
            continue
        if result.status == TestStatus.ERROR:
            entry.errors += 1
        else:
            entry.failed += 1
        if result.test_case.severity in {TestSeverity.CRITICAL, TestSeverity.ERROR}:
            entry.blocking_failures += 1
        entry.failed_names.append(result.test_case.name)
        if result.test_case.repair_strategy:
            entry.repair_strategies.append(result.test_case.repair_strategy)
    for excluded in run.excluded_checks:
        _add_exclusion(area(area_for_check(excluded.check_id, run.prefix)), excluded)
    return sorted(areas.values(), key=lambda a: (a.scope_type, a.scope_name))


def _add_exclusion(entry: TrustArea, excluded: ExcludedCheck) -> None:
    entry.checks_expected += 1
    entry.disabled_names.append(excluded.name)


def _listed(names: list[str]) -> str:
    shown = "; ".join(names[:_LISTED])
    return shown + (f"; and {len(names) - _LISTED} more" if len(names) > _LISTED else "")


def _clip(text: str) -> str:
    return text if len(text) <= _TEXT_LIMIT else text[: _TEXT_LIMIT - 3] + "..."


# ---------------------------------------------------------------------------
# Deployed modules and publishing
# ---------------------------------------------------------------------------


def deployed_modules_sql(prefix: str) -> str:
    return (
        "SELECT dpm.module_name\n"
        f"FROM {prefix}_SEM_STD_V.data_product_map AS dpm\n"
        "WHERE dpm.is_active = 1\n"
        "  AND UPPER(dpm.deployment_status) = 'DEPLOYED'\n"
        "ORDER BY dpm.module_name;"
    )


def read_deployed_modules(adapter, prefix: str) -> list[str]:
    """Deployed module names from the product's data_product_map ([] if unreadable)."""
    try:
        rows = adapter.fetch_all(deployed_modules_sql(prefix))
    except Exception as exc:  # noqa: BLE001 - the map still publishes the checked areas
        LOGGER.warning("Could not read deployed modules for %s: %s", prefix, exc)
        return []
    names = []
    for row in rows:
        value = next((v for k, v in row.items() if str(k).lower() == "module_name"), None)
        if value:
            canonical = next(
                (m for m in STANDARD_MODULES if m.lower() == str(value).strip().lower()), None
            )
            names.append(canonical or str(value).strip())
    return names


def trust_area_map_sql(run: ValidationRun, areas: list[TrustArea], table_name: str) -> str:
    """One multi-statement request: clear the map, then insert every area."""
    qualified_table = _qualified_identifier(table_name)
    measured = _timestamp_literal(run.completed_at)
    columns = (
        "scope_type, scope_name, coverage, status, confidence, gaps, recommendation, measured_dts"
    )
    statements = [f"DELETE FROM {qualified_table}"]
    for entry in areas:
        coverage = "NULL" if entry.coverage is None else f"{entry.coverage:.4f}"
        values = ", ".join(
            [
                _sql_literal(entry.scope_type),
                _sql_literal(entry.scope_name),
                coverage,
                _sql_literal(entry.status),
                _sql_literal(entry.confidence),
                _sql_literal(entry.gaps),
                _sql_literal(entry.recommendation),
                measured,
            ]
        )
        statements.append(f"INSERT INTO {qualified_table} ({columns}) VALUES ({values})")
    return ";\n".join(statements) + ";"


def publish_trust_area_map(
    adapter: PublishAdapter,
    run: ValidationRun,
    table_name: str | None = None,
) -> tuple[str, list[TrustArea]]:
    """Publish the run's trust heatmap; returns the table and the areas written."""
    qualified_table = _qualified_identifier(table_name or default_trust_area_table(run.prefix))
    areas = build_trust_areas(run, read_deployed_modules(adapter, run.prefix))
    adapter.execute(trust_area_map_sql(run, areas, qualified_table))
    return qualified_table, areas
