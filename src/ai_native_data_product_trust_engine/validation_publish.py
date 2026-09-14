"""Publish a run as the standard validation result: validation_run and validation_area.

Binding of the AI-Native Data Product validation pattern
(ai-native-data-products ``design/patterns/validation.md``, wire schema 2.1,
Teradata files ``implementation/teradata/patterns/validation/``):

    validation_run    one row per run per producer: the advisory summary
    validation_area   one row per run per area: the per-area trust map

Both are append-only operational evidence in the Observability module. The
standard's ``validation_latest`` and ``validation_trust_map`` views read them;
``validation_trust_map`` joins every area row to its run, so both are
published together in one multi-statement request. Nothing here is a gate:
``agent_use_allowed`` is published as 1 (deprecated at 2.1, VAL-02).

Areas
    Every check belongs to one area, assigned by the Trust Engine's check-family
    profile (the token after ``{PREFIX}-`` in a test id), never by a data
    object's name. Scope ids come from the standard corpus:

        MODULE      the product's registered module name (data_product_map)
        PATTERN     a pattern anchor: object-placement, temporal-lifecycle-metadata
        CAPABILITY  a catalogued capability: NearestNeighbors
        PRODUCT     the product prefix, for whole-product checks

    A check whose module the product does not register falls back to the
    PRODUCT area, so every MODULE scope id resolves (VAL-14). Every registered
    module gets a row even when no check covers it, so an unvalidated module
    reads as no-evidence rather than sound (VAL-18).

Status and confidence follow §4.3; checks disabled by rule configuration count
as expected but not run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ai_native_data_product_trust_engine.models import (
    ExcludedCheck,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.repairs import RepairCandidate
from ai_native_data_product_trust_engine.trust_publish import (
    PublishAdapter,
    _json_text,
    _publish_row,
    _qualified_identifier,
    _sql_literal,
    _timestamp_literal,
)

LOGGER = logging.getLogger(__name__)

PAYLOAD_SCHEMA_VERSION = "2.1"
PRODUCER_ID = "adp-trust-engine"
SOURCE_FORMAT = "NATIVE"

MODULE = "MODULE"
PATTERN = "PATTERN"
CAPABILITY = "CAPABILITY"
PRODUCT = "PRODUCT"

#: Check family to (scope_kind, scope_id). None as scope_id means the product prefix.
AREA_PROFILE: dict[str, tuple[str, str | None]] = {
    "SEM": (MODULE, "Semantic"),
    "DISCOVERY": (MODULE, "Semantic"),
    "TEXT": (MODULE, "Semantic"),
    "OPS": (MODULE, "Observability"),
    "QUERY": (MODULE, "Memory"),
    "REL": (MODULE, "Domain"),
    "RELATIONSHIP": (MODULE, "Domain"),
    "VIEW": (PATTERN, "object-placement"),
    "STD": (PATTERN, "object-placement"),
    "BUS": (PATTERN, "object-placement"),
    "TEMPORAL": (PATTERN, "temporal-lifecycle-metadata"),
    "CAP": (CAPABILITY, "NearestNeighbors"),
    "CAPABILITY": (CAPABILITY, "NearestNeighbors"),
    "STRUCT": (PRODUCT, None),
    "PERF": (PRODUCT, None),
}

#: Individual checks whose area differs from their family's.
AREA_OVERRIDES: dict[str, tuple[str, str | None]] = {
    "SEM-011": (MODULE, "Observability"),  # lineage endpoints live in Observability
    "TEXT-004": (MODULE, "Memory"),  # Query_Cookbook free text
}

#: Disabled scanner families (``SCANNER:<id>`` exclusions) to their area.
SCANNER_AREAS: dict[str, tuple[str, str | None]] = {
    "CAPABILITY": (CAPABILITY, "NearestNeighbors"),
    "QUERY": (MODULE, "Memory"),
    "RELATIONSHIP": (MODULE, "Domain"),
    "TEXT": (MODULE, "Semantic"),
    "VIEW": (PATTERN, "object-placement"),
}

_TEXT_LIMIT = 1000
_LISTED = 3


def default_validation_database(prefix: str) -> str:
    return f"{prefix}_OBS_STD_T"


def producer_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("ai-native-data-product-trust-engine")
    except Exception:  # noqa: BLE001 - version is descriptive, never required
        return None


# ---------------------------------------------------------------------------
# Area assignment
# ---------------------------------------------------------------------------


def area_for_check(
    check_id: str, prefix: str, registered_modules: list[str] | None = None
) -> tuple[str, str]:
    """(scope_kind, scope_id) for a check; unregistered modules fall back to PRODUCT."""
    upper_id = check_id.upper()
    if upper_id.startswith("SCANNER:"):
        kind, scope = SCANNER_AREAS.get(upper_id.split(":", 1)[1], (PRODUCT, None))
    else:
        stem = (
            upper_id[len(prefix) + 1 :] if upper_id.startswith(f"{prefix.upper()}-") else upper_id
        )
        kind, scope = next(
            (
                area
                for override, area in AREA_OVERRIDES.items()
                if stem == override or stem.startswith(f"{override}-")
            ),
            AREA_PROFILE.get(stem.split("-", 1)[0], (PRODUCT, None)),
        )
    if kind == MODULE and registered_modules is not None:
        registered = {m.upper(): m for m in registered_modules}
        if scope.upper() not in registered:
            return PRODUCT, prefix
        scope = registered[scope.upper()]
    return kind, scope if scope is not None else prefix


# ---------------------------------------------------------------------------
# Area evaluation (validation.md §4.3)
# ---------------------------------------------------------------------------


@dataclass
class ValidationArea:
    """One validation_area row: a cell of the trust map."""

    scope_kind: str
    scope_id: str
    checks_expected: int = 0
    passed_count: int = 0
    failed_count: int = 0
    error_count: int = 0
    critical_failure_count: int = 0
    error_failure_count: int = 0
    failed_names: list[str] = field(default_factory=list)
    repair_strategies: list[str] = field(default_factory=list)
    disabled_names: list[str] = field(default_factory=list)
    unregistered_modules: set[str] = field(default_factory=set)

    @property
    def checks_ran(self) -> int:
        return self.passed_count + self.failed_count + self.error_count

    @property
    def area_status(self) -> str:
        if self.checks_expected == 0:
            return "no-evidence"
        if self.checks_ran == 0:
            return "not-validated"
        if self.failed_count or self.error_count:
            return "fail"
        return "pass" if self.checks_ran == self.checks_expected else "partial"

    @property
    def confidence(self) -> str:
        if self.checks_ran == 0:
            return "unknown"
        if (
            self.critical_failure_count
            or self.error_failure_count
            or self.error_count
            or self.checks_ran * 2 < self.checks_expected
        ):
            return "weak"
        if self.failed_count or self.checks_ran < self.checks_expected:
            return "partial"
        return "strong"

    @property
    def open_gaps(self) -> str | None:
        if self.confidence == "strong":
            return None
        parts = []
        if self.checks_expected == 0:
            parts.append(f"No Trust Engine check covers {self._label()}.")
        if self.failed_count or self.error_count:
            outcome = []
            if self.failed_count:
                outcome.append(f"{self.failed_count} failed")
            if self.error_count:
                outcome.append(f"{self.error_count} could not run")
            parts.append(
                f"{' and '.join(outcome)} of {self.checks_ran} checks: "
                f"{_listed(self.failed_names)}."
            )
        if self.disabled_names:
            parts.append(
                f"{len(self.disabled_names)} of {self.checks_expected} checks disabled by rule "
                f"configuration: {_listed(self.disabled_names)}."
            )
        if self.unregistered_modules:
            parts.append(
                "Checks for modules not registered in data_product_map are reported at product "
                f"level: {', '.join(sorted(self.unregistered_modules))}."
            )
        if not parts and self.checks_ran < self.checks_expected:
            parts.append(f"{self.checks_ran} of {self.checks_expected} checks ran.")
        return _clip(" ".join(parts))

    @property
    def recommended_action(self) -> str | None:
        if self.confidence == "strong":
            return None
        if self.repair_strategies:
            return _clip(self.repair_strategies[0])
        if self.disabled_names:
            return _clip("Re-enable the disabled checks, or record why this area is out of scope.")
        return _clip(
            f"Add Trust Engine checks for {self._label()} so its trust is evidenced rather than "
            "unknown."
        )

    def _label(self) -> str:
        return {
            MODULE: f"the {self.scope_id} module",
            PATTERN: f"the {self.scope_id} pattern",
            CAPABILITY: f"the {self.scope_id} capability",
        }.get(self.scope_kind, "the product as a whole")


def build_validation_areas(
    run: ValidationRun, registered_modules: list[str] | None = None
) -> list[ValidationArea]:
    """Resolve a run into one area per covered or registered area."""
    areas: dict[tuple[str, str], ValidationArea] = {}

    def area(key: tuple[str, str]) -> ValidationArea:
        if key not in areas:
            areas[key] = ValidationArea(scope_kind=key[0], scope_id=key[1])
        return areas[key]

    for module in registered_modules or []:
        area((MODULE, module))
    for result in run.results:
        entry = area(area_for_check(result.test_case.test_id, run.prefix, registered_modules))
        _note_unregistered(entry, result.test_case.test_id, run.prefix, registered_modules)
        entry.checks_expected += 1
        if result.status == TestStatus.PASSED:
            entry.passed_count += 1
            continue
        if result.status == TestStatus.ERROR:
            entry.error_count += 1
        else:
            entry.failed_count += 1
        if result.test_case.severity == TestSeverity.CRITICAL:
            entry.critical_failure_count += 1
        elif result.test_case.severity == TestSeverity.ERROR:
            entry.error_failure_count += 1
        entry.failed_names.append(result.test_case.name)
        if result.test_case.repair_strategy:
            entry.repair_strategies.append(result.test_case.repair_strategy)
    for excluded in run.excluded_checks:
        entry = area(area_for_check(excluded.check_id, run.prefix, registered_modules))
        _add_exclusion(entry, excluded)
    return sorted(areas.values(), key=lambda a: (a.scope_kind, a.scope_id))


def _note_unregistered(
    entry: ValidationArea, check_id: str, prefix: str, registered_modules: list[str] | None
) -> None:
    """Record the module a PRODUCT-level check was meant for, when it isn't registered."""
    if entry.scope_kind != PRODUCT or registered_modules is None:
        return
    intended_kind, intended_scope = area_for_check(check_id, prefix, None)
    if intended_kind == MODULE:
        entry.unregistered_modules.add(intended_scope)


def _add_exclusion(entry: ValidationArea, excluded: ExcludedCheck) -> None:
    entry.checks_expected += 1
    entry.disabled_names.append(excluded.name)


def _listed(names: list[str]) -> str:
    shown = "; ".join(names[:_LISTED])
    return shown + (f"; and {len(names) - _LISTED} more" if len(names) > _LISTED else "")


def _clip(text: str) -> str:
    return text if len(text) <= _TEXT_LIMIT else text[: _TEXT_LIMIT - 3] + "..."


# ---------------------------------------------------------------------------
# Registered modules
# ---------------------------------------------------------------------------


def registered_modules_sql(prefix: str) -> str:
    return (
        "SELECT dpm.module_name\n"
        f"FROM {prefix}_SEM_STD_V.data_product_map AS dpm\n"
        "WHERE dpm.is_active = 1\n"
        "ORDER BY dpm.module_name;"
    )


def read_registered_modules(adapter, prefix: str) -> list[str] | None:
    """Module names the product registers, or None when data_product_map is unreadable."""
    try:
        rows = adapter.fetch_all(registered_modules_sql(prefix))
    except Exception as exc:  # noqa: BLE001 - areas still publish, unverified
        LOGGER.warning("Could not read data_product_map for %s: %s", prefix, exc)
        return None
    names = []
    for row in rows:
        value = next((v for k, v in row.items() if str(k).lower() == "module_name"), None)
        if value and str(value).strip():
            names.append(str(value).strip())
    return names


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_RUN_COLUMNS = (
    "product_prefix",
    "producer_id",
    "producer_version",
    "profile_id",
    "profile_version",
    "source_format",
    "payload_schema_version",
    "run_id",
    "started_dts",
    "completed_dts",
    "trust_status",
    "agent_use_allowed",
    "total_checks",
    "passed_count",
    "failed_count",
    "error_count",
    "critical_failure_count",
    "error_failure_count",
    "data_product_trust_score",
    "performance_readiness_score",
    "operational_readiness_score",
    "repair_candidate_count",
    "failed_checks_json",
    "repair_candidates_json",
    "evidence_expires_dts",
)

_AREA_COLUMNS = (
    "product_prefix",
    "producer_id",
    "run_id",
    "scope_kind",
    "scope_id",
    "checks_expected",
    "checks_ran",
    "passed_count",
    "failed_count",
    "error_count",
    "critical_failure_count",
    "error_failure_count",
    "area_status",
    "confidence",
    "open_gaps",
    "recommended_action",
    "completed_dts",
)


def validation_run_row(
    run: ValidationRun,
    repair_candidates: list[RepairCandidate],
    registered_modules: list[str] | None = None,
) -> dict[str, object | None]:
    """The validation_run row at wire schema 2.1."""
    row = dict(_publish_row(run, repair_candidates))
    row.update(
        {
            "producer_id": PRODUCER_ID,
            "producer_version": producer_version(),
            "profile_id": None,
            "profile_version": None,
            "source_format": SOURCE_FORMAT,
            "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
            # Deprecated at 2.1: published as 1 and never a decision (VAL-02).
            "agent_use_allowed": 1,
            "failed_checks_json": _scoped_failed_checks_json(run, registered_modules),
            "evidence_expires_dts": None,
        }
    )
    return row


def _scoped_failed_checks_json(run: ValidationRun, registered_modules: list[str] | None) -> str:
    """Failed-check items carrying scope_kind and scope_id (wire schema 2.1)."""
    items = []
    for result in run.results:
        if result.status not in {TestStatus.FAILED, TestStatus.ERROR}:
            continue
        case = result.test_case
        kind, scope = area_for_check(case.test_id, run.prefix, registered_modules)
        items.append(
            {
                "test_id": case.test_id,
                "name": case.name,
                "category": case.category.value,
                "severity": case.severity.value,
                "status": result.status.value,
                "row_count": result.row_count,
                "sample_rows": result.sample_rows[:3],
                "error_message": result.error_message,
                "repair_strategy": case.repair_strategy,
                "scope_kind": kind,
                "scope_id": scope,
            }
        )
    return _json_text(json.loads(json.dumps(items[:20], default=str)))


def validation_publish_sql(
    run: ValidationRun,
    repair_candidates: list[RepairCandidate],
    areas: list[ValidationArea],
    database: str,
    registered_modules: list[str] | None = None,
) -> str:
    """One multi-statement request: the run row, then one row per area (append-only)."""
    run_table = _qualified_identifier(f"{database}.validation_run")
    area_table = _qualified_identifier(f"{database}.validation_area")
    run_row = validation_run_row(run, repair_candidates, registered_modules)
    run_values = ", ".join(_run_value(column, run_row[column]) for column in _RUN_COLUMNS)
    statements = [f"INSERT INTO {run_table} ({', '.join(_RUN_COLUMNS)}) VALUES ({run_values})"]
    for entry in areas:
        values = {
            "product_prefix": run.prefix,
            "producer_id": PRODUCER_ID,
            "run_id": run_row["run_id"],
            "scope_kind": entry.scope_kind,
            "scope_id": entry.scope_id,
            "checks_expected": entry.checks_expected,
            "checks_ran": entry.checks_ran,
            "passed_count": entry.passed_count,
            "failed_count": entry.failed_count,
            "error_count": entry.error_count,
            "critical_failure_count": entry.critical_failure_count,
            "error_failure_count": entry.error_failure_count,
            "area_status": entry.area_status,
            "confidence": entry.confidence,
            "open_gaps": entry.open_gaps,
            "recommended_action": entry.recommended_action,
        }
        rendered = [_sql_literal(values[c]) for c in _AREA_COLUMNS if c != "completed_dts"]
        rendered.append(_timestamp_literal(run.completed_at))
        statements.append(
            f"INSERT INTO {area_table} ({', '.join(_AREA_COLUMNS)}) VALUES ({', '.join(rendered)})"
        )
    return ";\n".join(statements) + ";"


def _run_value(column: str, value: object | None) -> str:
    if column in {"failed_checks_json", "repair_candidates_json"}:
        return f"CAST({_sql_literal(value)} AS JSON)"
    if column in {"started_dts", "completed_dts"}:
        return _timestamp_literal(value)
    if column == "evidence_expires_dts" and value is not None:
        return _timestamp_literal(value)
    return _sql_literal(value)


def publish_validation(
    adapter: PublishAdapter,
    run: ValidationRun,
    repair_candidates: list[RepairCandidate],
    database: str | None = None,
) -> tuple[str, list[ValidationArea]]:
    """Append the run and its trust map; returns the database and the areas written."""
    target = database or default_validation_database(run.prefix)
    registered = read_registered_modules(adapter, run.prefix)
    areas = build_validation_areas(run, registered)
    adapter.execute(validation_publish_sql(run, repair_candidates, areas, target, registered))
    return target, areas
