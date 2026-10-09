"""Platform layout resolution (Platform Layout Standard, sections 5 to 7).

A reader of a data product resolves its physical names from the product's own
declared layout, by layer role and binding, never from a suffix or pattern in a
name. This module owns that resolution for the Trust Engine (the "evaluator").

Layer roles are platform neutral: ``STORAGE`` (the base table), ``ACCESS`` (the
optional governed 1:1 view) and ``CONSUMER`` (the governed interface for agents
and tools). On Teradata the legacy conventions map ``_STD_T`` to STORAGE,
``_STD_V`` to ACCESS and ``_BUS_V`` to CONSUMER.

Every value resolves from the first source that supplies it:

1. invocation        CLI flags (``--semantic-namespace`` and friends)
2. configuration     the ``layout`` object of the rules config
3. declaration       read from the product itself (registry row, data_product_map,
                     governance.data_product_container, access_object)
4. derivation        the naming convention the engine hard-coded before this module
                     existed, reported as ``LAYOUT_NOT_DECLARED``

``derive_layout`` is priority 4 on its own and reproduces the legacy SQL byte for
byte. ``resolve_layout`` reads the declaration from a live adapter, probing for
every table and column first and degrading to derivation when one is absent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

ROLE_STORAGE = "STORAGE"
ROLE_ACCESS = "ACCESS"
ROLE_CONSUMER = "CONSUMER"
LAYER_ROLES = (ROLE_STORAGE, ROLE_ACCESS, ROLE_CONSUMER)

SOURCE_INVOCATION = "invocation"
SOURCE_CONFIGURATION = "configuration"
SOURCE_DECLARATION = "declaration"
SOURCE_DERIVATION = "derivation"

ISSUE_LAYOUT_NOT_DECLARED = "LAYOUT_NOT_DECLARED"
PLATFORM_TERADATA = "teradata"

# Governance layer_code -> layer role (overridable in the rules config).
DEFAULT_LAYER_CODE_MAP: dict[str, str] = {
    "BASE": ROLE_STORAGE,
    "VIEW": ROLE_ACCESS,
    "ACCESS": ROLE_CONSUMER,
    "BUSINESS": ROLE_CONSUMER,
}

# access_object.object_type -> layer role.
_OBJECT_TYPE_ROLES = {
    "TABLE": ROLE_STORAGE,
    "BASE_VIEW": ROLE_ACCESS,
    "CONSUMER_VIEW": ROLE_CONSUMER,
}

MODULE_SEMANTIC = "SEMANTIC"
MODULE_OBSERVABILITY = "OBSERVABILITY"
MODULE_MEMORY = "MEMORY"
# Pseudo-module for declared containers that no module row claims.
ANY_MODULE = "*"

DEFAULT_REGISTRY_DATABASE = "DataProductsMaster_GOV_BUS_V"
DEFAULT_REGISTRY_VIEW = "active_data_product_registry"
DEFAULT_GRAPH_CATALOGUE_DATABASE = "Graphs_CAT_STD_0_T"

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")

# Values a product is expected to declare itself. Anything in this list that
# resolves at priority 4 is reported by LAYOUT-001.
DECLARABLE_VALUES = (
    "platform_profile",
    "standard_version",
    "layer_bindings",
    "semantic_database",
    "observability_database",
    "memory_database",
)


@dataclass(frozen=True)
class LayoutOverrides:
    """Priority 1 (invocation) or priority 2 (rules config) values.

    ``None`` means "not supplied at this level", so the next priority applies.
    """

    semantic_database: str | None = None
    observability_database: str | None = None
    memory_database: str | None = None
    registry_database: str | None = None
    registry_view: str | None = None
    graph_catalogue_database: str | None = None
    # Where governance.data_product_container lives, when it is not found next to
    # the registry.
    governance_database: str | None = None
    layer_code_map: Mapping[str, str] | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, name) is None for name in _OVERRIDE_FIELDS)


_OVERRIDE_FIELDS = (
    "semantic_database",
    "observability_database",
    "memory_database",
    "registry_database",
    "registry_view",
    "graph_catalogue_database",
    "governance_database",
    "layer_code_map",
)


@dataclass(frozen=True)
class ModuleContainers:
    """The containers of one module, by layer role."""

    module: str
    storage: tuple[str, ...] = ()
    access: tuple[str, ...] = ()
    consumer: tuple[str, ...] = ()
    # Containers the product records for the module without a layer role.
    unclassified: tuple[str, ...] = ()

    def all_containers(self) -> tuple[str, ...]:
        return _unique((*self.storage, *self.access, *self.consumer, *self.unclassified))

    def to_dict(self) -> dict[str, object]:
        return {
            "storage": list(self.storage),
            "access": list(self.access),
            "consumer": list(self.consumer),
            "unclassified": list(self.unclassified),
        }


@dataclass(frozen=True)
class CurrentViewBinding:
    """The CONSUMER object that serves an entity's current state."""

    entity_name: str
    database_name: str
    object_name: str
    access_semantics: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.database_name}.{self.object_name}"

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_name": self.entity_name,
            "view": self.qualified_name,
            "access_semantics": self.access_semantics,
        }


@dataclass(frozen=True)
class Layout:
    """Resolved physical names for one data product."""

    prefix: str
    semantic_database: str
    semantic_storage_database: str
    semantic_consumer_database: str
    observability_database: str
    observability_storage_database: str
    observability_consumer_database: str
    memory_database: str
    registry_database: str = DEFAULT_REGISTRY_DATABASE
    registry_view: str = DEFAULT_REGISTRY_VIEW
    graph_catalogue_database: str = DEFAULT_GRAPH_CATALOGUE_DATABASE
    # Module -> containers by layer role. Empty means no layer bindings were
    # declared, so the legacy suffix derivation applies wherever a container set
    # would otherwise be used.
    modules: Mapping[str, ModuleContainers] = field(default_factory=dict)
    consumer_containers: tuple[str, ...] = ()
    current_views: Mapping[str, CurrentViewBinding] = field(default_factory=dict)
    layer_code_map: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_LAYER_CODE_MAP))
    platform_profile: str | None = None
    standard_version: str | None = None
    declared: bool = False
    # True when the declaration reader located the product (a registry row or a
    # Semantic data_product_map). Without it there is nothing to infer a
    # declaration from, so LAYOUT-001 has nothing to report.
    product_found: bool = False
    sources: Mapping[str, str] = field(default_factory=dict)
    source_detail: Mapping[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    # ------------------------------------------------------------------ roles

    @property
    def uses_container_sets(self) -> bool:
        """True when declared layer bindings replace the suffix derivation."""
        return bool(self.modules)

    @property
    def access_containers(self) -> tuple[str, ...]:
        return _unique(c for m in self.modules.values() for c in m.access)

    @property
    def storage_containers(self) -> tuple[str, ...]:
        return _unique(c for m in self.modules.values() for c in m.storage)

    @property
    def access_layer_declared(self) -> bool:
        """False only when a declared layout has no ACCESS container at all."""
        return not self.uses_container_sets or bool(self.access_containers)

    def module_of(self, container: str) -> ModuleContainers | None:
        wanted = container.strip().upper()
        for module in self.modules.values():
            if any(name.upper() == wanted for name in module.all_containers()):
                return module
        return None

    def source_of(self, name: str) -> str:
        return self.sources.get(name, SOURCE_DERIVATION)

    def undeclared_values(self) -> list[str]:
        """Values the run needed that the product did not declare (priority 4)."""
        return [name for name in DECLARABLE_VALUES if self.source_of(name) == SOURCE_DERIVATION]

    # ------------------------------------------------------------ SQL helpers

    def module_scope_filter(self, database_expression: str) -> str:
        """SQL predicate: ``database_expression`` is a container of a deployed module."""
        sem_db = self.semantic_database
        if not self.uses_container_sets:
            return f"""
EXISTS (
    SELECT 1
    FROM {sem_db}.data_product_map module_scope
    WHERE COALESCE(module_scope.is_active, 1) = 1
      AND UPPER(COALESCE(TRIM(module_scope.deployment_status), 'DEPLOYED')) = 'DEPLOYED'
      AND (
          UPPER(TRIM(module_scope.database_name)) = UPPER(TRIM({database_expression}))
          OR UPPER(OREPLACE(OREPLACE(TRIM(module_scope.database_name), '_STD_T', '_STD_V'), '_BUS_V', '_STD_V'))
                = UPPER(TRIM({database_expression}))
          OR UPPER(OREPLACE(OREPLACE(TRIM(module_scope.database_name), '_STD_T', '_BUS_V'), '_STD_V', '_BUS_V'))
                = UPPER(TRIM({database_expression}))
      )
)""".strip()
        member_lines = "".join(
            self._module_member_clause(name, module, database_expression)
            for name, module in self.modules.items()
            if module.all_containers()
        )
        return f"""
EXISTS (
    SELECT 1
    FROM {sem_db}.data_product_map module_scope
    WHERE COALESCE(module_scope.is_active, 1) = 1
      AND UPPER(COALESCE(TRIM(module_scope.deployment_status), 'DEPLOYED')) = 'DEPLOYED'
      AND (
          UPPER(TRIM(module_scope.database_name)) = UPPER(TRIM({database_expression})){member_lines}
      )
)""".strip()

    @staticmethod
    def _module_member_clause(name: str, module: ModuleContainers, expression: str) -> str:
        members = f"UPPER(TRIM({expression})) IN ({_in_list(module.all_containers())})"
        if name == ANY_MODULE:
            return f"\n          OR {members}"
        return (
            f"\n          OR (UPPER(TRIM(module_scope.module_name)) = {sql_string(name)}"
            f"\n              AND {members})"
        )

    def consumer_database_expression(self, physical_database_expression: str) -> str:
        """SQL expression: the CONSUMER container serving a physical container."""
        if not self.uses_container_sets:
            return (
                f"OREPLACE(OREPLACE({physical_database_expression}, '_STD_T', '_BUS_V'), "
                "'_STD_V', '_BUS_V')"
            )
        pairs = []
        for module in self.modules.values():
            if not module.consumer:
                continue
            target = module.consumer[0]
            for name in module.all_containers():
                pairs.append((name, target))
        if not pairs:
            return physical_database_expression
        whens = " ".join(
            f"WHEN {sql_string(name.upper())} THEN {sql_string(target)}" for name, target in pairs
        )
        return (
            f"CASE UPPER(TRIM({physical_database_expression})) {whens} "
            f"ELSE {physical_database_expression} END"
        )

    def product_scope(self, database_expression: str) -> str:
        """SQL predicate: ``database_expression`` belongs to this product."""
        if not self.uses_container_sets:
            prefix = self.prefix.replace("'", "''")
            return f"{database_expression} LIKE '{prefix}\\_%' ESCAPE '\\'"
        names = _unique(c for m in self.modules.values() for c in m.all_containers())
        return _member_sql(database_expression, names)

    def access_database_predicate(self, database_expression: str) -> str:
        """SQL predicate: ``database_expression`` is an ACCESS container."""
        if not self.uses_container_sets:
            prefix = self.prefix.replace("'", "''")
            return f"{database_expression} LIKE '{prefix}\\_%\\_STD\\_V' ESCAPE '\\'"
        return _member_sql(database_expression, self.access_containers)

    def consumer_database_predicate(self, database_expression: str) -> str:
        """SQL predicate: ``database_expression`` is a CONSUMER container."""
        if not self.uses_container_sets:
            prefix = self.prefix.replace("'", "''")
            return f"{database_expression} LIKE '{prefix}\\_%\\_BUS\\_V' ESCAPE '\\'"
        return _member_sql(database_expression, self.consumer_containers)

    def storage_database_predicate(self, database_expression: str) -> str:
        """SQL predicate: a STORAGE container whose module also has an ACCESS container."""
        if not self.uses_container_sets:
            prefix = self.prefix.replace("'", "''")
            return f"{database_expression} LIKE '{prefix}\\_%\\_STD\\_T' ESCAPE '\\'"
        names = _unique(c for m in self.modules.values() if m.access for c in m.storage)
        return _member_sql(database_expression, names)

    def expected_access_database_expression(self, storage_expression: str) -> str:
        """SQL expression: the ACCESS container expected over a STORAGE container."""
        if not self.uses_container_sets:
            return (
                f"TRIM(SUBSTRING({storage_expression} FROM 1 FOR "
                f"CHARACTER_LENGTH({storage_expression}) - 6) || '_STD_V')"
            )
        whens = " ".join(
            f"WHEN {sql_string(name.upper())} THEN {sql_string(module.access[0])}"
            for module in self.modules.values()
            if module.access
            for name in module.storage
        )
        if not whens:
            return "CAST(NULL AS VARCHAR(128))"
        return f"CASE UPPER(TRIM({storage_expression})) {whens} END"

    def base_database_for_access(self, access_database: str) -> str:
        """The STORAGE container behind an ACCESS container."""
        if not self.uses_container_sets:
            return access_database.removesuffix("_STD_V") + "_STD_T"
        module = self.module_of(access_database)
        if module and module.storage:
            return module.storage[0]
        return access_database

    def not_consumer_endpoint(self, database_expression: str) -> str:
        """SQL predicate: ``database_expression`` is NOT a CONSUMER container."""
        if not self.uses_container_sets:
            return f"UPPER({database_expression}) NOT LIKE '%\\_BUS\\_V' ESCAPE '\\'"
        if not self.consumer_containers:
            return "1 = 1"
        return f"UPPER(TRIM({database_expression})) NOT IN ({_in_list(self.consumer_containers)})"

    def current_view_expression(self, entity_name_expression: str, fallback_expression: str) -> str:
        """SQL expression: the declared current-state view of an entity, else the fallback."""
        if not self.current_views:
            return fallback_expression
        whens = " ".join(
            f"WHEN {sql_string(key)} THEN {sql_string(binding.qualified_name)}"
            for key, binding in self.current_views.items()
        )
        return f"CASE UPPER(TRIM({entity_name_expression})) {whens} ELSE {fallback_expression} END"

    def governed_access_database(self, database_name: str) -> str:
        """The ACCESS container to read when a relationship names a STORAGE container."""
        if not self.uses_container_sets:
            if database_name.upper().endswith("_STD_T"):
                return database_name[:-6] + "_STD_V"
            return database_name
        module = self.module_of(database_name)
        if (
            module
            and module.access
            and database_name.upper() in {c.upper() for c in module.storage}
        ):
            return module.access[0]
        return database_name

    def storage_database_for(self, database_name: str) -> str:
        """The STORAGE container behind a view container (repair DML targets tables)."""
        if not self.uses_container_sets:
            if database_name.endswith("_STD_V"):
                return _safe_identifier(database_name.removesuffix("_STD_V") + "_STD_T")
            return _safe_identifier(database_name)
        module = self.module_of(database_name)
        if (
            module
            and module.storage
            and database_name.upper() not in {c.upper() for c in module.storage}
        ):
            return _safe_identifier(module.storage[0])
        return _safe_identifier(database_name)

    def consumer_label(self) -> str:
        """Human label for the consumer layer in generated hints."""
        return "consumer" if self.uses_container_sets else "BUS_V"

    # ---------------------------------------------------------------- report

    def to_dict(self) -> dict[str, object]:
        return {
            "prefix": self.prefix,
            "declared": self.declared,
            "product_found": self.product_found,
            "platform_profile": self.platform_profile,
            "standard_version": self.standard_version,
            "resolution_order": [
                SOURCE_INVOCATION,
                SOURCE_CONFIGURATION,
                SOURCE_DECLARATION,
                SOURCE_DERIVATION,
            ],
            "names": {
                "semantic_database": self.semantic_database,
                "semantic_storage_database": self.semantic_storage_database,
                "semantic_consumer_database": self.semantic_consumer_database,
                "observability_database": self.observability_database,
                "observability_storage_database": self.observability_storage_database,
                "observability_consumer_database": self.observability_consumer_database,
                "memory_database": self.memory_database,
                "registry_database": self.registry_database,
                "registry_view": self.registry_view,
                "graph_catalogue_database": self.graph_catalogue_database,
            },
            "sources": dict(self.sources),
            "source_detail": dict(self.source_detail),
            "modules": {name: module.to_dict() for name, module in self.modules.items()},
            "consumer_containers": list(self.consumer_containers),
            "current_views": [binding.to_dict() for binding in self.current_views.values()],
            "layer_code_map": dict(self.layer_code_map),
            "undeclared_values": self.undeclared_values(),
            "notes": list(self.notes),
        }

    def names_match_derivation(self) -> bool:
        """True when every name and binding is what plain derivation would produce.

        Such a layout generates exactly the legacy SQL, so callers may use the
        legacy entry points unchanged.
        """
        derived = derive_layout(self.prefix)
        return (
            not self.uses_container_sets
            and not self.current_views
            and all(
                getattr(self, name) == getattr(derived, name)
                for name in (
                    "semantic_database",
                    "semantic_storage_database",
                    "semantic_consumer_database",
                    "observability_database",
                    "observability_storage_database",
                    "observability_consumer_database",
                    "memory_database",
                    "registry_database",
                    "registry_view",
                    "graph_catalogue_database",
                )
            )
        )

    def summary(self) -> str:
        profile = self.platform_profile or "undeclared"
        version = self.standard_version or "undeclared"
        state = "declared" if self.declared else "not declared"
        return f"layout {state} (platform {profile}, standard {version})"


# --------------------------------------------------------------------------
# Priority 4: derivation
# --------------------------------------------------------------------------


def derive_layout(prefix: str) -> Layout:
    """The legacy Teradata naming convention, exactly as the engine used it."""
    return Layout(
        prefix=prefix,
        semantic_database=f"{prefix}_SEM_STD_V",
        semantic_storage_database=f"{prefix}_SEM_STD_T",
        semantic_consumer_database=f"{prefix}_SEM_BUS_V",
        observability_database=f"{prefix}_OBS_STD_V",
        observability_storage_database=f"{prefix}_OBS_STD_T",
        observability_consumer_database=f"{prefix}_OBS_BUS_V",
        memory_database=f"{prefix}_MEM_STD_V",
        sources=dict.fromkeys(_ALL_VALUES, SOURCE_DERIVATION),
        source_detail={},
    )


_ALL_VALUES = (
    "platform_profile",
    "standard_version",
    "layer_bindings",
    "current_views",
    "layer_code_map",
    "semantic_database",
    "semantic_storage_database",
    "semantic_consumer_database",
    "observability_database",
    "observability_storage_database",
    "observability_consumer_database",
    "memory_database",
    "registry_database",
    "registry_view",
    "graph_catalogue_database",
)


# --------------------------------------------------------------------------
# Priorities 1 and 2: overrides applied to a layout
# --------------------------------------------------------------------------


def apply_overrides(
    layout: Layout,
    configuration: LayoutOverrides | None = None,
    invocation: LayoutOverrides | None = None,
) -> Layout:
    """Apply priority 2 then priority 1 values on top of ``layout``.

    Used directly by ``generate-tests`` (no database, so no priority 3) and by
    :func:`resolve_layout` after the declaration has been merged.
    """
    names = (
        "semantic_database",
        "observability_database",
        "memory_database",
        "registry_database",
        "registry_view",
        "graph_catalogue_database",
    )
    sources = dict(layout.sources)
    detail = dict(layout.source_detail)
    values: dict[str, object] = {}
    layer_code_map = dict(layout.layer_code_map)
    for level, overrides in (
        (SOURCE_CONFIGURATION, configuration),
        (SOURCE_INVOCATION, invocation),
    ):
        if overrides is None:
            continue
        for name in names:
            value = getattr(overrides, name)
            if value:
                values[name] = value
                sources[name] = level
                detail.pop(name, None)
        if overrides.layer_code_map:
            layer_code_map.update({k.upper(): v for k, v in overrides.layer_code_map.items()})
            sources["layer_code_map"] = level
    return replace(
        layout,
        **values,
        layer_code_map=layer_code_map,
        sources=sources,
        source_detail=detail,
    )


# --------------------------------------------------------------------------
# Priority 3: read the product's declaration
# --------------------------------------------------------------------------


def resolve_layout(
    adapter,
    prefix: str,
    overrides: LayoutOverrides | None = None,
    invocation: LayoutOverrides | None = None,
) -> Layout:
    """Resolve the product layout in the standard's four-level order.

    ``invocation`` is priority 1 (CLI flags), ``overrides`` priority 2 (rules
    config). With ``adapter=None`` only overrides and derivation apply.
    """
    configuration = overrides or LayoutOverrides()
    invoked = invocation or LayoutOverrides()
    base = derive_layout(prefix)
    # The code map affects how the declaration is read, so settle it first.
    code_map = dict(DEFAULT_LAYER_CODE_MAP)
    for level_overrides in (configuration, invoked):
        if level_overrides.layer_code_map:
            code_map.update({k.upper(): v for k, v in level_overrides.layer_code_map.items()})
    if adapter is None:
        return apply_overrides(replace(base, layer_code_map=code_map), configuration, invoked)

    reader = _DeclarationReader(adapter, prefix, configuration, invoked, code_map)
    declaration = reader.read()
    merged = _merge_declaration(base, declaration, code_map)
    return apply_overrides(merged, configuration, invoked)


@dataclass
class _Declaration:
    notes: list[str] = field(default_factory=list)
    registry: dict[str, object] | None = None
    # module -> role -> containers, from data_product_container and access_object
    roles: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # module -> containers recorded in data_product_map / the registry (no role)
    unclassified: dict[str, list[str]] = field(default_factory=dict)
    # consumer containers anywhere (access_object rows that name no module)
    consumer_anywhere: list[str] = field(default_factory=list)
    current_views: dict[str, CurrentViewBinding] = field(default_factory=dict)
    semantic_read_database: str | None = None


class _DeclarationReader:
    def __init__(
        self,
        adapter,
        prefix: str,
        configuration: LayoutOverrides,
        invocation: LayoutOverrides,
        code_map: Mapping[str, str],
    ) -> None:
        self.adapter = adapter
        self.prefix = prefix
        self.code_map = code_map
        self.configuration = configuration
        self.invocation = invocation
        self.declaration = _Declaration()

    # -- helpers -------------------------------------------------------------

    def _override(self, name: str, default: str) -> str:
        return getattr(self.invocation, name) or getattr(self.configuration, name) or default

    def _rows(self, sql: str, what: str) -> list[dict[str, object]]:
        try:
            rows = self.adapter.fetch_all(sql)
        except Exception as exc:  # noqa: BLE001 - a missing declaration degrades, never aborts
            self.declaration.notes.append(f"{what} could not be read: {_first_line(str(exc))}")
            return []
        return [{str(k).lower(): v for k, v in row.items()} for row in rows]

    def _columns(self, database: str, tables: tuple[str, ...]) -> dict[str, set[str]]:
        table_list = ", ".join(sql_string(t) for t in tables)
        rows = self._rows(
            "SELECT TRIM(TableName) AS table_name, TRIM(ColumnName) AS column_name\n"
            "FROM DBC.ColumnsV\n"
            f"WHERE DatabaseName = {sql_string(database)}\n"
            f"  AND TableName IN ({table_list})",
            f"column catalogue of {database}",
        )
        columns: dict[str, set[str]] = {}
        for row in rows:
            table = str(row.get("table_name") or "").strip().lower()
            column = str(row.get("column_name") or "").strip().lower()
            if table and column:
                columns.setdefault(table, set()).add(column)
        return columns

    # -- reading -------------------------------------------------------------

    def read(self) -> _Declaration:
        registry_database = self._override("registry_database", DEFAULT_REGISTRY_DATABASE)
        registry_view = self._override("registry_view", DEFAULT_REGISTRY_VIEW)
        self._read_registry(registry_database, registry_view)
        semantic = self._find_semantic_database()
        self.declaration.semantic_read_database = semantic
        if semantic:
            self._read_semantic(semantic)
        self._read_containers(registry_database)
        return self.declaration

    def _read_registry(self, database: str, view: str) -> None:
        if not (IDENTIFIER_PATTERN.fullmatch(database) and IDENTIFIER_PATTERN.fullmatch(view)):
            self.declaration.notes.append(f"registry {database}.{view} is not a valid name")
            return
        columns = self._columns(database, (view,)).get(view.lower())
        if not columns:
            self.declaration.notes.append(f"registry {database}.{view} was not found")
            return
        wanted = [
            name
            for name in (
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
            )
            if name in columns
        ]
        known = _unique(
            [
                self._override("semantic_database", f"{self.prefix}_SEM_STD_V"),
                f"{self.prefix}_SEM_STD_V",
                f"{self.prefix}_SEM_STD_T",
            ]
        )
        match = []
        if "product_id" in columns:
            match.append(f"UPPER(TRIM(product_id)) = UPPER({sql_string(self.prefix)})")
        if "product_name" in columns:
            match.append(f"UPPER(TRIM(product_name)) LIKE UPPER({sql_string(self.prefix + '%')})")
        for column in ("semantic_database", "semantic_view_database"):
            if column in columns:
                match.append(f"{column} IN ({', '.join(sql_string(k) for k in known)})")
        if not match:
            self.declaration.notes.append(f"registry {database}.{view} has no identifying columns")
            return
        status = (
            "\n  AND UPPER(TRIM(product_status)) = 'ACTIVE'" if "product_status" in columns else ""
        )
        rows = self._rows(
            f"SELECT {', '.join(wanted)}\nFROM {database}.{view}\n"
            f"WHERE ({' OR '.join(match)}){status}",
            f"registry row in {database}.{view}",
        )
        if not rows:
            self.declaration.notes.append(f"no registry row matches product {self.prefix}")
            return
        self.declaration.registry = min(rows, key=self._rank)

    def _rank(self, row: dict[str, object]) -> int:
        if str(row.get("product_id") or "").strip().upper() == self.prefix.upper():
            return 0
        if str(row.get("product_name") or "").strip().upper().startswith(self.prefix.upper()):
            return 1
        return 2

    def _registry_text(self, column: str) -> str | None:
        value = (self.declaration.registry or {}).get(column)
        text = str(value).strip() if value is not None else ""
        return text or None

    def _find_semantic_database(self) -> str | None:
        candidates = _unique(
            [
                name
                for name in (
                    self.invocation.semantic_database,
                    self.configuration.semantic_database,
                    self._registry_text("semantic_view_database"),
                    self._registry_text("semantic_database"),
                    f"{self.prefix}_SEM_STD_V",
                    f"{self.prefix}_SEM_STD_T",
                )
                if name and IDENTIFIER_PATTERN.fullmatch(name)
            ]
        )
        if not candidates:
            return None
        rows = self._rows(
            "SELECT TRIM(DatabaseName) AS database_name\n"
            "FROM DBC.TablesV\n"
            "WHERE TableName = 'data_product_map'\n"
            f"  AND DatabaseName IN ({', '.join(sql_string(c) for c in candidates)})",
            "Semantic database probe",
        )
        present = {str(r.get("database_name") or "").strip().upper() for r in rows}
        for candidate in candidates:
            if candidate.upper() in present:
                return candidate
        self.declaration.notes.append("no Semantic data_product_map was found")
        return None

    def _read_semantic(self, semantic: str) -> None:
        columns = self._columns(semantic, ("data_product_map", "access_object"))
        map_columns = columns.get("data_product_map", set())
        if {"module_name", "database_name"} <= map_columns:
            filters = []
            if "is_active" in map_columns:
                filters.append("COALESCE(is_active, 1) = 1")
            if "deployment_status" in map_columns:
                filters.append("UPPER(COALESCE(TRIM(deployment_status), 'DEPLOYED')) = 'DEPLOYED'")
            where = f"\nWHERE {' AND '.join(filters)}" if filters else ""
            for row in self._rows(
                f"SELECT TRIM(module_name) AS module_name, TRIM(database_name) AS database_name\n"
                f"FROM {semantic}.data_product_map{where}",
                "data_product_map",
            ):
                self._add_unclassified(row.get("module_name"), row.get("database_name"))
        access_columns = columns.get("access_object", set())
        if {"database_name", "object_name"} <= access_columns:
            self._read_access_objects(semantic, access_columns)

    def _read_access_objects(self, semantic: str, columns: set[str]) -> None:
        wanted = [
            name
            for name in (
                "database_name",
                "object_name",
                "object_type",
                "consumer_audience",
                "access_semantics",
                "represents_entity",
                "is_active",
            )
            if name in columns
        ]
        where = "\nWHERE COALESCE(is_active, 1) = 1" if "is_active" in columns else ""
        rows = self._rows(
            f"SELECT {', '.join(wanted)}\nFROM {semantic}.access_object{where}",
            "access_object",
        )
        declaration = self.declaration
        by_entity: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            database = _text(row.get("database_name"))
            object_name = _text(row.get("object_name"))
            if not database or not object_name:
                continue
            role = _OBJECT_TYPE_ROLES.get(_text(row.get("object_type")).upper())
            if role is None:
                continue
            module = self._module_for_container(database)
            if module:
                _add_role(declaration.roles, module, role, database)
            elif role == ROLE_CONSUMER:
                declaration.consumer_anywhere.append(database)
            if role == ROLE_CONSUMER:
                entity = _text(row.get("represents_entity")).upper()
                if entity:
                    by_entity.setdefault(entity, []).append(row)
        for entity, objects in by_entity.items():
            binding = _current_binding(entity, objects)
            if binding:
                declaration.current_views[entity] = binding

    def _read_containers(self, registry_database: str) -> None:
        governance = self._override("governance_database", "")
        stem = re.sub(r"^(.*_GOV)_.*$", r"\1", registry_database)
        if governance:
            location = f"DatabaseName = {sql_string(governance)}"
        else:
            like = f"{stem}\\_%"
            location = (
                f"(DatabaseName = {sql_string(registry_database)} "
                f"OR DatabaseName LIKE {sql_string(like)} ESCAPE '\\')"
            )
        rows = self._rows(
            "SELECT TRIM(DatabaseName) AS database_name\n"
            "FROM DBC.TablesV\n"
            f"WHERE TableName = 'data_product_container'\n  AND {location}",
            "governance database probe",
        )
        databases = [
            _text(r.get("database_name"))
            for r in rows
            if IDENTIFIER_PATTERN.fullmatch(_text(r.get("database_name")))
        ]
        if not databases:
            self.declaration.notes.append("governance.data_product_container was not found")
            return
        for database in databases:
            columns = self._columns(database, ("data_product_container",)).get(
                "data_product_container", set()
            )
            if not {"module_name", "layer_code", "container_name"} <= columns:
                continue
            filters = [
                f"container_name LIKE {sql_string(self.prefix + chr(92) + '_%')} ESCAPE '\\'"
            ]
            for flag, value in (("is_active", 1), ("is_current", 1), ("is_deleted", 0)):
                if flag in columns:
                    filters.append(f"COALESCE({flag}, {value}) = {value}")
            found = self._rows(
                "SELECT TRIM(module_name) AS module_name, TRIM(layer_code) AS layer_code, "
                "TRIM(container_name) AS container_name\n"
                f"FROM {database}.data_product_container\nWHERE {' AND '.join(filters)}",
                f"{database}.data_product_container",
            )
            for row in found:
                role = self.code_map.get(_text(row.get("layer_code")).upper())
                module = _text(row.get("module_name")).upper()
                container = _text(row.get("container_name"))
                if role in LAYER_ROLES and module and container:
                    _add_role(self.declaration.roles, module, role, container)
            if found:
                return

    def _add_unclassified(self, module: object, database: object) -> None:
        name = _text(module).upper()
        container = _text(database)
        if name and container:
            self.declaration.unclassified.setdefault(name, []).append(container)

    def _module_for_container(self, container: str) -> str | None:
        wanted = container.upper()
        for module, roles in self.declaration.roles.items():
            if any(c.upper() == wanted for values in roles.values() for c in values):
                return module
        for module, values in self.declaration.unclassified.items():
            if any(c.upper() == wanted for c in values):
                return module
        return None


def _current_binding(entity: str, objects: list[dict[str, object]]) -> CurrentViewBinding | None:
    def semantics(row: dict[str, object]) -> str:
        return _text(row.get("access_semantics")).upper()

    def audience_rank(row: dict[str, object]) -> int:
        return 0 if _text(row.get("consumer_audience")).upper() in {"AGENT", "ALL", ""} else 1

    current = [row for row in objects if semantics(row) == "CURRENT_ONLY"]
    pool = current or objects
    chosen = min(pool, key=audience_rank)
    return CurrentViewBinding(
        entity_name=entity,
        database_name=_text(chosen.get("database_name")),
        object_name=_text(chosen.get("object_name")),
        access_semantics=semantics(chosen) or None,
    )


def _merge_declaration(
    base: Layout, declaration: _Declaration, code_map: Mapping[str, str]
) -> Layout:
    sources = dict(base.sources)
    detail: dict[str, str] = {}
    registry = declaration.registry
    values: dict[str, object] = {}

    # Module containers by role.
    modules: dict[str, ModuleContainers] = {}
    for name, roles in declaration.roles.items():
        modules[name] = ModuleContainers(
            module=name,
            storage=_unique(roles.get(ROLE_STORAGE, [])),
            access=_unique(roles.get(ROLE_ACCESS, [])),
            consumer=_unique(roles.get(ROLE_CONSUMER, [])),
            unclassified=_unique(declaration.unclassified.get(name, [])),
        )
    if declaration.consumer_anywhere:
        modules[ANY_MODULE] = ModuleContainers(
            module=ANY_MODULE, consumer=_unique(declaration.consumer_anywhere)
        )
    consumers = _unique(c for m in modules.values() for c in m.consumer)
    if modules:
        sources["layer_bindings"] = SOURCE_DECLARATION
        detail["layer_bindings"] = "governance.data_product_container / access_object"
    if declaration.current_views:
        sources["current_views"] = SOURCE_DECLARATION
        detail["current_views"] = "access_object CONSUMER objects (CURRENT_ONLY preferred)"

    def registry_text(column: str) -> str | None:
        value = (registry or {}).get(column)
        text = str(value).strip() if value is not None else ""
        return text if text and IDENTIFIER_PATTERN.fullmatch(text) else None

    def first(items: list[tuple[str | None, str]]) -> tuple[str, str] | None:
        for value, where in items:
            if value:
                return value, where
        return None

    for module_key, read_name, storage_name, consumer_name, prefix_column in (
        (
            MODULE_SEMANTIC,
            "semantic_database",
            "semantic_storage_database",
            "semantic_consumer_database",
            "semantic",
        ),
        (
            MODULE_OBSERVABILITY,
            "observability_database",
            "observability_storage_database",
            "observability_consumer_database",
            "observability",
        ),
        (MODULE_MEMORY, "memory_database", None, None, "memory"),
    ):
        module = modules.get(module_key)
        reads = [
            (
                registry_text(f"{prefix_column}_view_database"),
                f"registry.{prefix_column}_view_database",
            ),
            (module.access[0] if module and module.access else None, "ACCESS container"),
            (module.consumer[0] if module and module.consumer else None, "CONSUMER container"),
            (registry_text(f"{prefix_column}_database"), f"registry.{prefix_column}_database"),
        ]
        if module_key == MODULE_SEMANTIC and declaration.semantic_read_database:
            reads.insert(0, (declaration.semantic_read_database, "data_product_map probe"))
        picked = first(reads)
        if picked:
            values[read_name] = picked[0]
            sources[read_name] = SOURCE_DECLARATION
            detail[read_name] = picked[1]
        if storage_name:
            stored = first(
                [
                    (module.storage[0] if module and module.storage else None, "STORAGE container"),
                    (
                        registry_text(f"{prefix_column}_database"),
                        f"registry.{prefix_column}_database",
                    ),
                ]
            )
            if stored:
                values[storage_name] = stored[0]
                sources[storage_name] = SOURCE_DECLARATION
                detail[storage_name] = stored[1]
        if consumer_name and module and module.consumer:
            values[consumer_name] = module.consumer[0]
            sources[consumer_name] = SOURCE_DECLARATION
            detail[consumer_name] = "CONSUMER container"

    profile = _registry_value(registry, "platform_profile")
    version = _registry_value(registry, "standard_version")
    if profile:
        sources["platform_profile"] = SOURCE_DECLARATION
        detail["platform_profile"] = "registry.platform_profile"
    if version:
        sources["standard_version"] = SOURCE_DECLARATION
        detail["standard_version"] = "registry.standard_version"
    declared = bool(profile and version and sources["layer_bindings"] == SOURCE_DECLARATION)
    return replace(
        base,
        **values,
        modules=modules,
        consumer_containers=consumers,
        current_views=dict(declaration.current_views),
        layer_code_map=dict(code_map),
        platform_profile=profile,
        standard_version=version,
        declared=declared,
        product_found=bool(registry is not None or declaration.semantic_read_database),
        sources=sources,
        source_detail=detail,
        notes=tuple(declaration.notes),
    )


def _registry_value(registry: dict[str, object] | None, column: str) -> str | None:
    value = (registry or {}).get(column)
    text = str(value).strip() if value is not None else ""
    return text or None


def _add_role(
    roles: dict[str, dict[str, list[str]]], module: str, role: str, container: str
) -> None:
    bucket = roles.setdefault(module, {}).setdefault(role, [])
    if container not in bucket:
        bucket.append(container)


# --------------------------------------------------------------------------
# Exclusions (Platform Layout Standard section 7, rules 2 to 4)
# --------------------------------------------------------------------------

ACCESS_DEPENDENT_CHECKS = (
    ("STD-VIEW-1TO1", "Standard views are thin 1:1 table contracts"),
    ("STD-TABLE-VIEW-COVERAGE", "Standard tables have matching locking views"),
    ("STD-VIEW-COLUMN-CONTRACT", "Standard view columns match source tables"),
    ("BUS-VIEW-SOURCES", "Business views select from standard views"),
    ("VIEW-TABLE-LOCKING", "Views that query tables directly use access locks"),
)


def access_layer_exclusion_reason(layout: Layout) -> str:
    return (
        "The declared layout has no ACCESS container for any module, and the platform layout "
        "marks the ACCESS layer recommended rather than required, so this check does not apply "
        "here (Platform Layout Standard section 7, rule 2). It would fail if an ACCESS layer "
        "were declared and broken."
    )


def access_dependent_check_ids(prefix: str) -> list[str]:
    return [f"{prefix.upper()}-{suffix}" for suffix, _ in ACCESS_DEPENDENT_CHECKS]


def layout_excluded_check_ids(prefix: str, layout: Layout | None) -> frozenset[str]:
    """Check ids that do not apply to this layout (none under the derived layout)."""
    if layout is None or layout.access_layer_declared:
        return frozenset()
    return frozenset(access_dependent_check_ids(prefix))


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def sql_string(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _member_sql(expression: str, names) -> str:
    """``expression`` is one of ``names`` (case-insensitive); false when there are none."""
    if not names:
        return "1 = 0"
    return f"UPPER(TRIM({expression})) IN ({_in_list(names)})"


def _in_list(values) -> str:
    return ", ".join(sql_string(str(value).upper()) for value in values)


def _unique(values) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for value in values:
        key = str(value).upper()
        if value and key not in seen:
            seen[key] = str(value)
    return tuple(seen.values())


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _first_line(message: str) -> str:
    return message.strip().splitlines()[0][:200] if message.strip() else "unknown error"


def _safe_identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        msg = f"[ADPTrust.InvalidIdentifier] Unsafe SQL identifier {value}."
        raise ValueError(msg)
    return value
