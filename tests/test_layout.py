"""Platform Layout Standard adoption: resolution order, declared layouts, exclusions."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ai_native_data_product_trust_engine import cli
from ai_native_data_product_trust_engine.capabilities import capability_test_cases
from ai_native_data_product_trust_engine.html_reports import render_html_report
from ai_native_data_product_trust_engine.layout import (
    LayoutOverrides,
    apply_overrides,
    derive_layout,
    resolve_layout,
)
from ai_native_data_product_trust_engine.layout_checks import (
    layout_check_result,
    layout_excluded_checks,
)
from ai_native_data_product_trust_engine.mcp_server import build_orientation_resource
from ai_native_data_product_trust_engine.models import TestStatus
from ai_native_data_product_trust_engine.query_templates import query_template_test_cases
from ai_native_data_product_trust_engine.relationship_health import (
    _temporal_current_duplicate_sql,
    relationship_health_test_cases,
    run_temporal_current_validations,
)
from ai_native_data_product_trust_engine.repairs import generate_repair_candidates
from ai_native_data_product_trust_engine.reports import validation_run_to_dict
from ai_native_data_product_trust_engine.rule_config import load_rule_config
from ai_native_data_product_trust_engine.test_generation import generate_metadata_tests
from ai_native_data_product_trust_engine.text_references import text_reference_test_cases
from ai_native_data_product_trust_engine.trust_map import build_trust_map
from ai_native_data_product_trust_engine.trust_publish import (
    default_trust_table,
    default_trust_view,
    default_validation_database,
)
from ai_native_data_product_trust_engine.validation_ddl import default_validation_view_database
from ai_native_data_product_trust_engine.validators import run_validation
from ai_native_data_product_trust_engine.view_contracts import (
    run_view_contract_validations,
    view_contract_test_cases,
)
from layout_snapshot import PREFIX, build_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "default_layout_sql.json"

# --------------------------------------------------------------------------
# A stub that behaves like the declaration tables of a CallCentre-like product:
# consumer layer _ACL_V, Call_Current views, registry in DataProductCatalog_STD_V.
# --------------------------------------------------------------------------

REGISTRY_DB = "DataProductCatalog_STD_V"
REGISTRY_VIEW = "active_data_product_registry"
GOVERNANCE_DB = "DataProductCatalog_GOV_STD_V"

REGISTRY_COLUMNS = [
    "product_id",
    "product_name",
    "product_status",
    "semantic_database",
    "semantic_view_database",
    "memory_database",
    "memory_view_database",
    "observability_database",
    "observability_view_database",
    "platform_profile",
    "standard_version",
]
MAP_COLUMNS = ["module_name", "database_name", "deployment_status", "is_active", "primary_views"]
ACCESS_COLUMNS = [
    "database_name",
    "object_name",
    "object_type",
    "consumer_audience",
    "access_semantics",
    "represents_entity",
    "is_active",
]
CONTAINER_COLUMNS = ["module_name", "layer_code", "container_name", "is_active", "is_current"]

REGISTRY_ROW = {
    "product_id": "CallCentre",
    "product_name": "CallCentre Data Product",
    "product_status": "ACTIVE",
    "semantic_database": "CallCentre_SEM_STD_T",
    "semantic_view_database": "CallCentre_SEM_ACL_V",
    "memory_database": "CallCentre_MEM_STD_T",
    "memory_view_database": "CallCentre_MEM_ACL_V",
    "observability_database": "CallCentre_OBS_STD_T",
    "observability_view_database": "CallCentre_OBS_ACL_V",
    "platform_profile": "teradata",
    "standard_version": "1.0",
}
MODULE_CONTAINERS = [
    ("SEMANTIC", "BASE", "CallCentre_SEM_STD_T"),
    ("SEMANTIC", "ACCESS", "CallCentre_SEM_ACL_V"),
    ("DOMAIN", "BASE", "CallCentre_DOM_STD_T"),
    ("DOMAIN", "ACCESS", "CallCentre_DOM_ACL_V"),
    ("OBSERVABILITY", "BASE", "CallCentre_OBS_STD_T"),
    ("OBSERVABILITY", "ACCESS", "CallCentre_OBS_ACL_V"),
    ("MEMORY", "BASE", "CallCentre_MEM_STD_T"),
    ("MEMORY", "ACCESS", "CallCentre_MEM_ACL_V"),
]
ACCESS_OBJECTS = [
    {
        "database_name": "CallCentre_DOM_ACL_V",
        "object_name": "Call_Current",
        "object_type": "CONSUMER_VIEW",
        "consumer_audience": "AGENT",
        "access_semantics": "CURRENT_ONLY",
        "represents_entity": "Call",
        "is_active": 1,
    },
    {
        "database_name": "CallCentre_DOM_ACL_V",
        "object_name": "Call_History",
        "object_type": "CONSUMER_VIEW",
        "consumer_audience": "BI",
        "access_semantics": "FULL_HISTORY",
        "represents_entity": "Call",
        "is_active": 1,
    },
]


class DeclarationAdapter:
    """Answers the resolver's probes from in-memory declaration tables."""

    def __init__(
        self,
        *,
        registry_columns=REGISTRY_COLUMNS,
        registry_row=REGISTRY_ROW,
        containers=MODULE_CONTAINERS,
        access_objects=ACCESS_OBJECTS,
        semantic_db="CallCentre_SEM_ACL_V",
        map_columns=MAP_COLUMNS,
        access_columns=ACCESS_COLUMNS,
        container_columns=CONTAINER_COLUMNS,
        registry_db=REGISTRY_DB,
        governance_db=GOVERNANCE_DB,
        fail_on=(),
    ) -> None:
        self.registry_columns = registry_columns
        self.registry_row = registry_row
        self.containers = containers
        self.access_objects = access_objects
        self.semantic_db = semantic_db
        self.map_columns = map_columns
        self.access_columns = access_columns
        self.container_columns = container_columns
        self.registry_db = registry_db
        self.governance_db = governance_db
        self.fail_on = fail_on
        self.sql: list[str] = []

    def fetch_all(self, sql):
        self.sql.append(sql)
        for marker in self.fail_on:
            if marker in sql:
                raise RuntimeError(f"[Teradata Database] [Error 3807] {marker} does not exist")
        if "FROM DBC.ColumnsV" in sql:
            return self._columns(sql)
        if "FROM DBC.TablesV" in sql and "'data_product_map'" in sql:
            return [{"database_name": self.semantic_db}]
        if "FROM DBC.TablesV" in sql and "'data_product_container'" in sql:
            return [{"database_name": self.governance_db}] if self.containers is not None else []
        if f"FROM {self.registry_db}.{REGISTRY_VIEW}" in sql:
            return [dict(self.registry_row)] if self.registry_row else []
        if f"FROM {self.semantic_db}.data_product_map" in sql:
            return [
                {"module_name": module, "database_name": container}
                for module, layer, container in MODULE_CONTAINERS
                if layer == "BASE"
            ]
        if f"FROM {self.semantic_db}.access_object" in sql:
            return [dict(row) for row in (self.access_objects or [])]
        if f"FROM {self.governance_db}.data_product_container" in sql:
            return [
                {"module_name": m, "layer_code": layer, "container_name": c}
                for m, layer, c in (self.containers or [])
            ]
        return []

    def _columns(self, sql):
        database = re.search(r"DatabaseName = '([^']+)'", sql).group(1)
        tables = {
            REGISTRY_VIEW: self.registry_columns if database == self.registry_db else None,
            "data_product_map": self.map_columns if database == self.semantic_db else None,
            "access_object": self.access_columns if database == self.semantic_db else None,
            "data_product_container": (
                self.container_columns if database == self.governance_db else None
            ),
        }
        rows = []
        for table, columns in tables.items():
            if f"'{table}'" in sql or f"{table}" in sql.split("TableName IN")[-1]:
                for column in columns or []:
                    rows.append({"table_name": table, "column_name": column})
        return rows

    def fetch_all_with_session_setup(self, sql, setup_sql=None, teardown_sql=None):
        return self.fetch_all(sql)

    def execute(self, sql):
        raise AssertionError("resolution must never write")


CONFIG = LayoutOverrides(registry_database=REGISTRY_DB, registry_view=REGISTRY_VIEW)


def declared_layout(**adapter_options):
    return resolve_layout(DeclarationAdapter(**adapter_options), "CallCentre", CONFIG)


# --------------------------------------------------------------------------
# Default layout: the unchanged-behaviour guarantee
# --------------------------------------------------------------------------


@pytest.mark.parametrize("explicit", [False, True])
def test_default_layout_emits_exactly_the_legacy_sql(explicit):
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    extra = {"layout": derive_layout(PREFIX)} if explicit else {}

    actual = json.loads(json.dumps(build_snapshot(extra), sort_keys=True))

    assert actual == expected


def test_derived_layout_excludes_nothing_and_matches_derivation():
    layout = derive_layout("ExampleProduct")

    assert layout.names_match_derivation()
    assert layout_excluded_checks("ExampleProduct", layout) == []
    assert len(view_contract_test_cases("ExampleProduct", layout)) == 6
    assert layout.undeclared_values() == [
        "platform_profile",
        "standard_version",
        "layer_bindings",
        "semantic_database",
        "observability_database",
        "memory_database",
    ]


# --------------------------------------------------------------------------
# Priority order: invocation > configuration > declaration > derivation
# --------------------------------------------------------------------------


def test_declaration_overrides_derivation():
    layout = declared_layout()

    assert layout.semantic_database == "CallCentre_SEM_ACL_V"
    assert layout.sources["semantic_database"] == "declaration"
    assert layout.sources["platform_profile"] == "declaration"
    assert layout.platform_profile == "teradata"
    assert layout.standard_version == "1.0"
    assert layout.declared is True
    assert layout.consumer_containers == (
        "CallCentre_SEM_ACL_V",
        "CallCentre_DOM_ACL_V",
        "CallCentre_OBS_ACL_V",
        "CallCentre_MEM_ACL_V",
    )
    assert layout.current_views["CALL"].qualified_name == "CallCentre_DOM_ACL_V.Call_Current"


def test_configuration_overrides_declaration():
    configuration = LayoutOverrides(
        registry_database=REGISTRY_DB,
        registry_view=REGISTRY_VIEW,
        semantic_database="CallCentre_SEM_STD_T",
    )

    layout = resolve_layout(DeclarationAdapter(), "CallCentre", configuration)

    assert layout.semantic_database == "CallCentre_SEM_STD_T"
    assert layout.sources["semantic_database"] == "configuration"
    assert layout.sources["observability_database"] == "declaration"
    assert layout.observability_database == "CallCentre_OBS_ACL_V"


def test_invocation_overrides_configuration_and_declaration():
    configuration = LayoutOverrides(
        registry_database=REGISTRY_DB,
        registry_view=REGISTRY_VIEW,
        semantic_database="CallCentre_SEM_STD_T",
        memory_database="CallCentre_MEM_STD_T",
    )
    invocation = LayoutOverrides(semantic_database="CallCentre_SEM_CLI")

    layout = resolve_layout(DeclarationAdapter(), "CallCentre", configuration, invocation)

    assert layout.semantic_database == "CallCentre_SEM_CLI"
    assert layout.sources["semantic_database"] == "invocation"
    assert layout.memory_database == "CallCentre_MEM_STD_T"
    assert layout.sources["memory_database"] == "configuration"
    assert layout.sources["observability_database"] == "declaration"
    # Never written into the product.
    assert layout.sources["registry_database"] == "configuration"


def test_unsupplied_values_fall_back_to_derivation_and_are_recorded():
    layout = apply_overrides(
        derive_layout("CallCentre"), LayoutOverrides(memory_database="CallCentre_MEM_X")
    )

    assert layout.memory_database == "CallCentre_MEM_X"
    assert layout.sources["memory_database"] == "configuration"
    assert layout.semantic_database == "CallCentre_SEM_STD_V"
    assert layout.sources["semantic_database"] == "derivation"
    assert layout.registry_database == "DataProductsMaster_GOV_BUS_V"
    assert layout.sources["registry_database"] == "derivation"


def test_resolution_without_an_adapter_uses_overrides_and_derivation():
    layout = resolve_layout(None, "CallCentre", None, LayoutOverrides(registry_view="reg_v"))

    assert layout.registry_view == "reg_v"
    assert layout.sources["registry_view"] == "invocation"
    assert layout.product_found is False


# --------------------------------------------------------------------------
# Graceful degradation
# --------------------------------------------------------------------------


def test_missing_registry_degrades_to_derivation_with_a_note():
    adapter = DeclarationAdapter(
        registry_columns=[], registry_row=None, semantic_db="CallCentre_SEM_STD_V"
    )

    layout = resolve_layout(adapter, "CallCentre", CONFIG)

    assert layout.platform_profile is None
    assert layout.sources["platform_profile"] == "derivation"
    assert any("registry" in note and "not found" in note for note in layout.notes)


def test_registry_without_layout_columns_still_supplies_names():
    columns = [c for c in REGISTRY_COLUMNS if c not in {"platform_profile", "standard_version"}]
    row = {k: v for k, v in REGISTRY_ROW.items() if k in columns}

    adapter = DeclarationAdapter(registry_columns=columns, registry_row=row)
    layout = resolve_layout(adapter, "CallCentre", CONFIG)

    assert layout.platform_profile is None
    assert layout.standard_version is None
    assert layout.declared is False
    assert layout.sources["semantic_database"] == "declaration"
    assert layout.sources["platform_profile"] == "derivation"
    assert not any("platform_profile" in sql for sql in adapter.sql)


def test_missing_access_object_and_container_tables_leave_no_bindings():
    layout = declared_layout(access_columns=[], container_columns=[])

    assert layout.uses_container_sets is False
    assert layout.sources["layer_bindings"] == "derivation"
    assert layout.current_views == {}
    # Legacy derivation applies to everything that needs a binding.
    assert "OREPLACE" in layout.module_scope_filter("x")


def test_backend_errors_while_reading_never_abort_resolution():
    adapter = DeclarationAdapter(fail_on=("DBC.ColumnsV", "data_product_container"))

    layout = resolve_layout(adapter, "CallCentre", CONFIG)

    assert layout.semantic_database == "CallCentre_SEM_STD_V"
    assert layout.declared is False
    assert layout.notes


def test_resolution_only_reads():
    adapter = DeclarationAdapter()

    resolve_layout(adapter, "CallCentre", CONFIG)

    assert adapter.sql
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in adapter.sql)


# --------------------------------------------------------------------------
# CallCentre-like shape: declared names, no legacy derivations
# --------------------------------------------------------------------------

LEGACY_FRAGMENTS = (
    "OREPLACE(OREPLACE(TRIM(module_scope",
    "'_STD_T'",
    "'_STD_V'",
    "'_BUS_V'",
    "'_Current'",
    "NOT LIKE '%\\_BUS",
    "DataProductsMaster_GOV_BUS_V",
    "CallCentre_SEM_STD_V",
    "CallCentre_OBS_BUS_V",
    "CallCentre_MEM_STD_V",
)


def sql_by_id(tests):
    return {test.test_id.split("-", 1)[1]: test for test in tests}


def all_sql(test):
    return "\n".join(part for part in (test.sql, test.precondition_sql) if part)


def test_callcentre_metadata_checks_use_declared_names_only():
    layout = declared_layout()
    tests = sql_by_id(generate_metadata_tests("CallCentre", layout))

    for test_id, test in tests.items():
        text = all_sql(test)
        for fragment in LEGACY_FRAGMENTS:
            assert fragment not in text, f"{test_id} still derives {fragment}"
        assert "LIKE 'CallCentre\\_%'" not in text, test_id

    sem007 = all_sql(tests["SEM-007"])
    assert "FROM CallCentre_SEM_ACL_V.data_product_map" in sem007
    assert "'CallCentre_DOM_ACL_V'" in sem007  # consumer container of the DOMAIN module

    sem008 = all_sql(tests["SEM-008"])
    assert "WHEN 'CALL' THEN 'CallCentre_DOM_ACL_V.Call_Current'" in sem008
    assert "'CallCentre_SEM_STD_T' AS metadata_database_name" in sem008

    sem010 = tests["SEM-010"].sql
    assert "NOT IN (" in sem010 and "'CALLCENTRE_DOM_ACL_V'" in sem010

    ops003 = tests["OPS-003"].sql
    assert "'CallCentre_OBS_ACL_V' AS database_name" in ops003

    ops004 = all_sql(tests["OPS-004"])
    assert "FROM CallCentre_OBS_ACL_V.data_lineage" in ops004


def test_callcentre_registry_location_comes_from_configuration():
    layout = declared_layout()
    tests = sql_by_id(generate_metadata_tests("CallCentre", layout))

    assert f"{REGISTRY_DB}.{REGISTRY_VIEW}" in tests["DISCOVERY-001"].sql
    assert f"FROM {REGISTRY_DB}.{REGISTRY_VIEW}" in tests["DISCOVERY-002"].sql
    assert "DataProductsMaster" not in tests["DISCOVERY-002"].sql
    assert "standard_view_database_name" not in tests["DISCOVERY-002"].sql
    assert "IN ('CALLCENTRE_SEM_STD_T', 'CALLCENTRE_SEM_ACL_V')" in tests["DISCOVERY-002"].sql


def test_graph_catalogue_database_is_configurable():
    layout = apply_overrides(
        derive_layout("CallCentre"), LayoutOverrides(graph_catalogue_database="Estate_Graphs")
    )
    tests = sql_by_id(generate_metadata_tests("CallCentre", layout))

    for test_id in ("OPS-004", "OPS-005", "OPS-006"):
        text = all_sql(tests[test_id])
        assert "Graphs_CAT_STD_0_T" not in text
        assert "Estate_Graphs" in text


def test_callcentre_scanners_use_declared_names_only():
    layout = declared_layout()
    generated = [
        *capability_test_cases("CallCentre", layout),
        *query_template_test_cases("CallCentre", layout),
        *relationship_health_test_cases("CallCentre", layout),
        *text_reference_test_cases("CallCentre", layout),
        *view_contract_test_cases("CallCentre", layout),
    ]

    for test in generated:
        for fragment in LEGACY_FRAGMENTS:
            assert fragment not in all_sql(test), f"{test.test_id} still derives {fragment}"
        assert "_STD_V" not in all_sql(test), test.test_id
        assert "LIKE 'CallCentre\\_%" not in all_sql(test), test.test_id
    assert any("CallCentre_MEM_ACL_V.Query_Cookbook" in test.sql for test in generated)
    assert any("CallCentre_SEM_ACL_V.entity_metadata" in test.sql for test in generated)


def test_temporal_checks_follow_the_declared_current_view():
    layout = declared_layout()
    row = {
        "entity_name": "Call",
        "database_name": "CallCentre_DOM_STD_T",
        "table_name": "Call_H",
        "view_name": "CallCentre_DOM_ACL_V.Call_Current",
        "natural_key_column": "call_id",
        "current_flag_column": "is_current",
        "temporal_pattern": "TYPE_2_SCD",
    }

    class Temporal:
        def __init__(self):
            self.sql = []

        def fetch_all(self, sql):
            self.sql.append(sql)
            if "natural_key_column" in sql and "entity_metadata" in sql:
                return [row]
            if "RequestText" in sql:
                return [{"view_text": "SELECT * FROM x WHERE is_current = 1"}]
            return []

    adapter = Temporal()
    results = run_temporal_current_validations("CallCentre", adapter, layout)

    assert [r.status for r in results] == [TestStatus.PASSED]
    view_lookup = next(sql for sql in adapter.sql if "RequestText" in sql)
    assert "DatabaseName = 'CallCentre_DOM_ACL_V'" in view_lookup
    assert "TableName = 'Call_Current'" in view_lookup

    sql = _temporal_current_duplicate_sql(row, layout)
    # No ACCESS container is declared, so the table is read where it is.
    assert '"CallCentre_DOM_STD_T"."Call_H"' in sql
    assert "_STD_V" not in sql


def test_legacy_current_view_lookup_is_unchanged_for_undeclared_layouts():
    row = {
        "entity_name": "Customer",
        "database_name": "P_DOM_STD_T",
        "table_name": "Customer_H",
        "view_name": "Customer_Current",
        "natural_key_column": "customer_key",
        "current_flag_column": "is_current",
    }
    sql = _temporal_current_duplicate_sql(row, derive_layout("P"))

    assert '"P_DOM_STD_V"."Customer_H"' in sql


def test_publish_and_repair_defaults_follow_the_layout():
    layout = declared_layout()

    assert default_trust_table("CallCentre", layout) == "CallCentre_SEM_STD_T.trust_engine_run"
    assert default_trust_view("CallCentre", layout) == "CallCentre_SEM_ACL_V.trust_engine_latest"
    assert default_validation_database("CallCentre", layout) == "CallCentre_OBS_STD_T"
    assert default_validation_view_database("CallCentre", layout) == "CallCentre_OBS_ACL_V"
    assert layout.storage_database_for("CallCentre_MEM_ACL_V") == "CallCentre_MEM_STD_T"


# --------------------------------------------------------------------------
# ACCESS layer exclusions (section 7)
# --------------------------------------------------------------------------


def test_no_access_container_excludes_access_dependent_checks_with_a_reason():
    layout = declared_layout(
        containers=[row for row in MODULE_CONTAINERS if row[1] != "ACCESS"]
        + [(m, "ACCESS", c) for m, layer, c in MODULE_CONTAINERS if layer == "ACCESS"]
    )
    # Every ACCESS-coded container maps to CONSUMER by default; nothing maps to ACCESS.
    assert layout.access_containers == ()

    excluded = layout_excluded_checks("CallCentre", layout)

    assert {check.check_id for check in excluded} == {
        "CALLCENTRE-STD-VIEW-1TO1",
        "CALLCENTRE-STD-TABLE-VIEW-COVERAGE",
        "CALLCENTRE-STD-VIEW-COLUMN-CONTRACT",
        "CALLCENTRE-BUS-VIEW-SOURCES",
        "CALLCENTRE-VIEW-TABLE-LOCKING",
    }
    assert all("ACCESS container" in check.reason for check in excluded)
    assert all(check.counts_as_expected is False for check in excluded)
    ids = {test.test_id for test in view_contract_test_cases("CallCentre", layout)}
    assert ids == {"CALLCENTRE-VIEW-COLUMNS"}


def test_exclusions_are_reported_by_the_run_and_not_counted_as_expected():
    layout = declared_layout()

    class Views:
        def fetch_all(self, sql):
            if "TableKind = 'V'" in sql and "RequestText" not in sql:
                return [{"database_name": "CallCentre_DOM_ACL_V", "view_name": "Call_Current"}]
            return []

    run = run_validation(
        "CallCentre",
        Views(),
        [],
        include_capability_scans=False,
        include_query_template_scans=False,
        include_relationship_health_scans=False,
        include_text_reference_scans=False,
        layout=layout,
    )

    assert {r.test_case.test_id for r in run.results} == {
        "CALLCENTRE-VIEW-COLUMNS-CallCentre_DOM_ACL_V.Call_Current"
    } | {r.test_case.test_id for r in run.results if "LAYOUT" in r.test_case.test_id}
    assert len(run.excluded_checks) == 5
    entry = next(e for e in build_trust_map(run) if e.scope_id == "object-placement")
    assert entry.checks_expected == entry.checks_ran
    assert entry.area_status == "pass"
    report = validation_run_to_dict(run)
    assert report["summary"]["excluded"] == 5
    assert all("reason" in check for check in report["excluded_checks"])


def test_a_declared_access_layer_is_still_checked_and_fails_when_broken():
    containers = [
        *MODULE_CONTAINERS,
        ("DOMAIN", "VIEW", "CallCentre_DOM_STD_V"),
    ]
    layout = declared_layout(containers=containers)
    assert layout.access_containers == ("CallCentre_DOM_STD_V",)
    assert layout_excluded_checks("CallCentre", layout) == []

    class Broken:
        def fetch_all(self, sql):
            if "AS view_text" in sql and "STD_V" in sql.upper():
                return [
                    {
                        "database_name": "CallCentre_DOM_STD_V",
                        "view_name": "Call_H",
                        "view_text": "CREATE VIEW x AS SELECT * FROM CallCentre_DOM_STD_T.Call_H",
                    }
                ]
            if "TableKind = 'V'" in sql:
                return [{"database_name": "CallCentre_DOM_STD_V", "view_name": "Call_H"}]
            return []

    results = run_view_contract_validations("CallCentre", Broken(), layout)

    contract = next(r for r in results if "STD-VIEW-1TO1" in r.test_case.test_id)
    assert contract.status == TestStatus.FAILED
    assert {row["issue_code"] for row in contract.sample_rows} >= {"MISSING_LOCKING_ROW"}


def test_declared_inventories_use_container_membership():
    layout = declared_layout(
        containers=[*MODULE_CONTAINERS, ("DOMAIN", "VIEW", "CallCentre_DOM_STD_V")]
    )
    cases = {case.test_id: case for case in view_contract_test_cases("CallCentre", layout)}

    one_to_one = cases["CALLCENTRE-STD-VIEW-1TO1"].sql
    assert "UPPER(TRIM(DatabaseName)) IN ('CALLCENTRE_DOM_STD_V')" in one_to_one
    coverage = cases["CALLCENTRE-STD-TABLE-VIEW-COVERAGE"].sql
    assert "WHEN 'CALLCENTRE_DOM_STD_T' THEN 'CallCentre_DOM_STD_V'" in coverage
    assert "SUBSTRING" not in coverage
    business = cases["CALLCENTRE-BUS-VIEW-SOURCES"].sql
    assert "IN (" in business and "_BUS" not in business


# --------------------------------------------------------------------------
# LAYOUT-001
# --------------------------------------------------------------------------


def test_layout_check_reports_undeclared_values_with_a_repair_candidate():
    layout = resolve_layout(
        DeclarationAdapter(
            registry_columns=[c for c in REGISTRY_COLUMNS if c != "platform_profile"],
            registry_row={k: v for k, v in REGISTRY_ROW.items() if k != "platform_profile"},
        ),
        "CallCentre",
        CONFIG,
    )
    run = run_validation("CallCentre", DeclarationAdapter(), [], layout=layout)
    result = next(r for r in run.results if r.test_case.test_id == "CALLCENTRE-LAYOUT-001")

    assert result.status == TestStatus.FAILED
    assert result.test_case.severity.value == "WARNING"
    assert result.test_case.category.value == "SEMANTIC"
    assert {row["value_name"] for row in result.sample_rows} == {"platform_profile"}
    assert {row["issue_code"] for row in result.sample_rows} == {"LAYOUT_NOT_DECLARED"}

    candidates = [
        c for c in generate_repair_candidates(run) if c.issue_code == "LAYOUT_NOT_DECLARED"
    ]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.mode.value == "proposal"
    assert candidate.requires_approval is True
    assert f"UPDATE {REGISTRY_DB}.{REGISTRY_VIEW}" in candidate.sql
    assert "SET platform_profile = 'teradata'" in candidate.sql
    assert "standard_version = '1.0'" in candidate.sql  # read from the registry
    assert "WHERE product_id = 'CallCentre'" in candidate.sql


def test_layout_check_proposal_leaves_a_placeholder_when_no_standard_version_is_known():
    layout = derive_layout("ExampleProduct")
    from dataclasses import replace

    layout = replace(layout, product_found=True)
    run = run_validation("ExampleProduct", DeclarationAdapter(), [], layout=layout)

    candidate = next(
        c for c in generate_repair_candidates(run) if c.issue_code == "LAYOUT_NOT_DECLARED"
    )
    assert "SET platform_profile = 'teradata'" in candidate.sql
    assert "-- ,standard_version = '<Master Design version" in candidate.sql
    assert candidate.requires_approval is True


def test_a_fully_declared_layout_passes_the_layout_check():
    result = layout_check_result("CallCentre", declared_layout())

    assert result.status == TestStatus.PASSED
    assert result.sample_rows == []


def test_overridden_values_are_not_reported_as_undeclared():
    layout = resolve_layout(
        DeclarationAdapter(
            registry_row=None, registry_columns=[], semantic_db="CallCentre_SEM_STD_V"
        ),
        "CallCentre",
        LayoutOverrides(
            registry_database=REGISTRY_DB,
            registry_view=REGISTRY_VIEW,
            semantic_database="CallCentre_SEM_STD_V",
        ),
    )

    assert "semantic_database" not in layout.undeclared_values()
    assert "platform_profile" in layout.undeclared_values()


def test_the_layout_check_does_not_run_when_the_product_cannot_be_found():
    run = run_validation("Nothing", DeclarationAdapter(), [], layout=derive_layout("Nothing"))

    assert not any("LAYOUT" in r.test_case.test_id for r in run.results)


# --------------------------------------------------------------------------
# Rules config, CLI, reports
# --------------------------------------------------------------------------


def test_rules_config_layout_object_is_loaded_and_validated(tmp_path):
    config = tmp_path / "rules.json"
    config.write_text(
        json.dumps(
            {
                "layout": {
                    "semantic_database": "P_SEM_ACL_V",
                    "registry_database": "Catalog_STD_V",
                    "layer_code_map": {"acl": "consumer"},
                }
            }
        ),
        encoding="utf-8",
    )

    layout = load_rule_config(config).layout

    assert layout.semantic_database == "P_SEM_ACL_V"
    assert layout.registry_database == "Catalog_STD_V"
    assert layout.layer_code_map == {"ACL": "CONSUMER"}
    assert layout.memory_database is None


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"layout": "x"}, "must be an object"),
        ({"layout": {"bogus": "x"}}, "Unknown layout key: bogus"),
        ({"layout": {"semantic_database": "bad name"}}, "layout.semantic_database"),
        ({"layout": {"layer_code_map": {"X": "NOPE"}}}, "not a layer role"),
    ],
)
def test_rules_config_rejects_invalid_layout(tmp_path, payload, message):
    config = tmp_path / "rules.json"
    config.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_rule_config(config)


def test_a_rules_config_layer_code_map_changes_how_containers_are_classified():
    configuration = LayoutOverrides(
        registry_database=REGISTRY_DB,
        registry_view=REGISTRY_VIEW,
        layer_code_map={"ACCESS": "ACCESS", "VIEW": "ACCESS", "BASE": "STORAGE"},
    )

    layout = resolve_layout(DeclarationAdapter(), "CallCentre", configuration)

    assert layout.sources["layer_code_map"] == "configuration"
    assert layout.access_containers
    assert layout.consumer_containers == ("CallCentre_DOM_ACL_V",)


def test_validate_resolves_the_layout_once_and_passes_it_everywhere(monkeypatch, tmp_path):
    adapter = DeclarationAdapter()
    seen = {}

    monkeypatch.setattr(cli, "adapter_from_environment", lambda database_url=None: adapter)

    real_resolve = cli.resolve_layout
    calls = []

    def counting_resolve(*args, **kwargs):
        calls.append(args)
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(cli, "resolve_layout", counting_resolve)
    real_run = cli.run_validation

    def spying_run(prefix, adapter_, tests, **kwargs):
        seen["layout"] = kwargs["layout"]
        seen["sql"] = [t.sql for t in tests]
        return real_run(prefix, adapter_, tests, **kwargs)

    monkeypatch.setattr(cli, "run_validation", spying_run)
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps({"layout": {"registry_database": REGISTRY_DB, "registry_view": REGISTRY_VIEW}}),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    html = tmp_path / "report.html"

    cli.main(
        [
            "validate",
            "--prefix",
            "CallCentre",
            "--rules-config",
            str(rules),
            "--output",
            str(output),
            "--html-output",
            str(html),
            "--memory-namespace",
            "CallCentre_MEM_FLAG",
        ]
    )

    assert len(calls) == 1
    layout = seen["layout"]
    assert layout.memory_database == "CallCentre_MEM_FLAG"
    assert layout.sources["memory_database"] == "invocation"
    assert any("CallCentre_MEM_FLAG.Query_Cookbook" in sql for sql in seen["sql"])
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["layout"]["declared"] is True
    assert report["layout"]["platform_profile"] == "teradata"
    assert report["layout"]["sources"]["memory_database"] == "invocation"
    assert report["layout"]["sources"]["semantic_database"] == "declaration"
    page = html.read_text(encoding="utf-8")
    assert "Layout <b>declared; platform teradata; standard 1.0</b>" in page


def test_generate_tests_uses_invocation_overrides_and_derivation(monkeypatch, capsys):
    captured = {}
    real = cli.generate_metadata_tests

    def spy(prefix, layout=None):
        captured["layout"] = layout
        return real(prefix, layout)

    monkeypatch.setattr(cli, "generate_metadata_tests", spy)

    exit_code = cli.main(
        [
            "generate-tests",
            "--prefix",
            "CallCentre",
            "--semantic-namespace",
            "CallCentre_SEM_ACL_V",
            "--registry-database",
            "Catalog_STD_V",
            "--registry-view",
            "registry_v",
        ]
    )

    out = capsys.readouterr().out
    layout = captured["layout"]
    assert exit_code == 0
    assert layout.semantic_database == "CallCentre_SEM_ACL_V"
    assert layout.sources["semantic_database"] == "invocation"
    assert layout.registry_database == "Catalog_STD_V"
    assert layout.registry_view == "registry_v"
    assert layout.memory_database == "CallCentre_MEM_STD_V"
    assert layout.sources["memory_database"] == "derivation"
    assert "CALLCENTRE-LAYOUT-001" in out


def test_default_generate_tests_does_not_change_the_metadata_call(monkeypatch):
    calls = []

    def legacy_only(prefix):
        calls.append(prefix)
        return []

    monkeypatch.setattr(cli, "generate_metadata_tests", legacy_only)

    assert cli.main(["generate-tests", "--prefix", "ExampleProduct"]) == 0
    assert calls == ["ExampleProduct"]


def test_orientation_and_html_header_surface_the_layout():
    layout = declared_layout()
    run = run_validation("CallCentre", DeclarationAdapter(), [], layout=layout)
    report = json.loads(json.dumps(validation_run_to_dict(run)))

    summary = build_orientation_resource(report)["layout"]

    assert summary["declared"] is True
    assert summary["platform_profile"] == "teradata"
    assert summary["standard_version"] == "1.0"
    assert summary["sources"]["semantic_database"] == "declaration"
    assert summary["names"]["semantic_database"] == "CallCentre_SEM_ACL_V"
    assert {entry["check_id"] for entry in summary["excluded_for_layout"]} == {
        "CALLCENTRE-STD-VIEW-1TO1",
        "CALLCENTRE-STD-TABLE-VIEW-COVERAGE",
        "CALLCENTRE-STD-VIEW-COLUMN-CONTRACT",
        "CALLCENTRE-BUS-VIEW-SOURCES",
        "CALLCENTRE-VIEW-TABLE-LOCKING",
    }
    assert "Layout <b>declared; platform teradata; standard 1.0</b>" in render_html_report(run, [])
    assert build_orientation_resource({"prefix": "Old"})["layout"] is None
