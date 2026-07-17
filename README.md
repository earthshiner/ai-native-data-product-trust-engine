# AI-Native Data Product Trust Engine

A validation and self-healing engine for AI-native Data Products on Teradata.

The Trust Engine turns product metadata into an executable trust contract. It discovers deployed data product modules, generates validation tests, classifies failures, proposes deterministic repairs, and records validation evidence so agents, cookbooks, notebooks, and applications can rely on metadata with confidence.

## Mandate

The Trust Engine scores and improves **metadata trust**, not the intrinsic trustworthiness of
raw data values.

Its job is to answer a narrower and more agent-critical question:

> Does this AI-native Data Product's metadata accurately describe the deployed product that
> agents, cookbooks, notebooks, semantic search, and generated SQL rely on?

In scope:

- Object, view, column, relationship, path, glossary and capability metadata.
- SQL recipe validity and parameter readiness.
- Free-text references to objects, retired aliases, capabilities and business terms.
- View and metadata drift that can make generated assets fail.
- Deterministic repair proposals and safe self-healing for metadata defects.

Out of scope as a primary objective:

- General data quality scoring.
- Broad profiling of raw data values.
- Assessing whether business facts are true in the real world.

The engine may still run small data checks where they prove a metadata claim, such as mandatory
relationship orphan checks, current-record rules or required reference populations. Those checks are
evidence for metadata trust, not a replacement for a dedicated data quality platform.

## Goals

- Prove that semantic metadata matches deployed Teradata objects.
- Validate cookbook SQL, join paths, glossary references, capabilities, and view contracts.
- Detect drift between tables, views, metadata, generated recipes, and product capabilities.
- Apply safe deterministic repairs, and produce steward-approved repair proposals for everything else.
- Publish separate trust, performance readiness and operational readiness scores that can be
  consumed by agents and user-facing assets.

## Trust Model

The engine validates five contract areas:

1. Structural contract: databases, tables, views, columns, datatypes, and view drift.
2. Semantic contract: entities, column metadata, relationships, paths, glossary and design metadata.
3. Query contract: parameter binding, SQL parsing, EXPLAIN validation, smoke execution and expected result shape.
4. Capability contract: VECTOR, JSON, geospatial, ML, fallback patterns and product feature flags.
5. Evidence checks: targeted row-count, orphan, uniqueness, current-record and category checks
   only where they validate a metadata claim.

The report separates related but different readiness signals:

- **Data product trust score**: whether the product is correctly described, governed, aligned to
  deployed objects, and safe for agents to use.
- **Performance readiness score**: whether expected access paths are likely to run efficiently,
  including skew, statistics, expensive joins, bounded recipes and execution-plan risk.
- **Operational readiness score**: whether run-state signals such as freshness, observability,
  monitoring, SLA metadata and pipeline health are current.

Performance and operational checks remain visible in the same report, but they do not dilute the
data product trust score. A product can be trustworthy but slow, fast but untrustworthy, or
semantically sound while missing operational evidence.

## Self-Healing Levels

- Level 0: detect only.
- Level 1: propose repair.
- Level 2: safe metadata repair.
- Level 3: safe generated-object regeneration.
- Level 4: human-approved structural change.

The first implementation will default to proposal mode. Automatic repair is only allowed when the fix is deterministic, idempotent, and auditable.

## Initial CLI Shape

```powershell
python -m ai_native_data_product_trust_engine discover --prefix ProductPrefix
python -m ai_native_data_product_trust_engine generate-tests --prefix ProductPrefix
python -m ai_native_data_product_trust_engine validate --prefix ProductPrefix --repair-mode proposal
python -m ai_native_data_product_trust_engine report --prefix ProductPrefix
python -m ai_native_data_product_trust_engine mcp-server --reports-dir reports
```

During local development, run from the repository root with `src` on `PYTHONPATH`:

```powershell
$env:PYTHONPATH='src'
python -m ai_native_data_product_trust_engine generate-tests --prefix ProductPrefix
python -m ai_native_data_product_trust_engine validate --prefix ProductPrefix --output reports\productprefix-validation.json
python -m ai_native_data_product_trust_engine validate --prefix ProductPrefix --output reports\productprefix-validation.json --html-output reports\productprefix-validation.html
```

Live validation currently uses `DATABASE_URI` by default and writes JSON validation evidence. Use
`--html-output` to also create a standalone interactive HTML report for human review. Generated
reports are local artifacts and are not committed.

For troubleshooting backend failures, enable diagnostic logging. SQL is logged immediately before
execution, and failing SQL is logged again with the backend exception:

```powershell
python -m ai_native_data_product_trust_engine validate --prefix ProductPrefix --log-file logs\trust-engine.log
python -m ai_native_data_product_trust_engine validate --prefix ProductPrefix --log-level INFO
```

`--log-file` defaults to `INFO` level so the SQL statements are captured. Without `--log-file`, the
default level is `WARNING`; pass `--log-level INFO` to stream SQL diagnostics to the console.

For query cookbook performance diagnosis on Teradata, enable Optimizer HELPSTATS suggestions during
recipe `EXPLAIN` validation:

```powershell
python -m ai_native_data_product_trust_engine validate --prefix ProductPrefix --enable-helpstats --log-file logs\trust-engine.log
```

This runs `DIAGNOSTIC HELPSTATS ON FOR SESSION`, the recipe `EXPLAIN`, and
`DIAGNOSTIC HELPSTATS NOT ON FOR SESSION` on the same database session for each active cookbook
recipe. Any `COLLECT STATISTICS` suggestions in the `EXPLAIN` output are reported as advisory
`EXPLAIN_HELPSTATS_SUGGESTION` performance findings. The Trust Engine does not apply statistics
automatically; trial high-confidence single-column suggestions first, then rerun validation before
promoting them.

To make trust evidence cheap for agents to read at interaction time, deploy a compact history table
inside the data product and expose the latest row through the Semantic BUS_V access layer:

```sql
CREATE MULTISET TABLE {ProductPrefix}_SEM_STD_T.trust_engine_run
(
    product_prefix VARCHAR(128) CHARACTER SET LATIN NOT NULL,
    run_id VARCHAR(64) CHARACTER SET LATIN NOT NULL,
    started_dts TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    completed_dts TIMESTAMP(6) WITH TIME ZONE NOT NULL,
    trust_status VARCHAR(16) CHARACTER SET LATIN NOT NULL,
    agent_use_allowed BYTEINT NOT NULL,
    total_checks INTEGER NOT NULL,
    passed_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    error_count INTEGER NOT NULL,
    critical_failure_count INTEGER NOT NULL,
    error_failure_count INTEGER NOT NULL,
    data_product_trust_score INTEGER,
    performance_readiness_score INTEGER,
    operational_readiness_score INTEGER,
    repair_candidate_count INTEGER NOT NULL,
    failed_checks_json JSON(32000) CHARACTER SET UNICODE,
    repair_candidates_json JSON(32000) CHARACTER SET UNICODE
)
PRIMARY INDEX (product_prefix, completed_dts);

COLLECT STATISTICS COLUMN (product_prefix, completed_dts)
ON {ProductPrefix}_SEM_STD_T.trust_engine_run;

CREATE VIEW {ProductPrefix}_SEM_BUS_V.trust_engine_latest
(
    product_prefix,
    run_id,
    started_dts,
    completed_dts,
    trust_status,
    agent_use_allowed,
    total_checks,
    passed_count,
    failed_count,
    error_count,
    critical_failure_count,
    error_failure_count,
    data_product_trust_score,
    performance_readiness_score,
    operational_readiness_score,
    repair_candidate_count,
    failed_checks_json,
    repair_candidates_json
)
AS
LOCKING ROW FOR ACCESS
SELECT
    product_prefix,
    run_id,
    started_dts,
    completed_dts,
    trust_status,
    agent_use_allowed,
    total_checks,
    passed_count,
    failed_count,
    error_count,
    critical_failure_count,
    error_failure_count,
    data_product_trust_score,
    performance_readiness_score,
    operational_readiness_score,
    repair_candidate_count,
    failed_checks_json,
    repair_candidates_json
FROM {ProductPrefix}_SEM_STD_T.trust_engine_run
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY product_prefix
    ORDER BY completed_dts DESC, run_id DESC
) = 1;
```

Then publish after scheduled validation:

```powershell
python -m ai_native_data_product_trust_engine validate --prefix ProductPrefix --output reports\productprefix-validation.json --publish-trust-table
```

The publish target resolves in precedence order: an explicit two-part table name passed to
`--publish-trust-table`, then the rules-config `publish_trust_table` key, then the default
`{prefix}_SEM_STD_T.trust_engine_run`. The flag itself remains the publish trigger — a config
key alone never publishes. Products that home their validation evidence in the Observability
module (per the AI-Native validation-results direction) pin the target in their rules config
so every scheduled run lands in the right table. Agents should read the product's registered
trust view (by default `{prefix}_SEM_BUS_V.trust_engine_latest`) and treat
`agent_use_allowed = 0` or `trust_status = 'UNTRUSTED'` as a stop signal before generating SQL
over the product.

Optional rule configuration can disable specific generated tests or scanner families, and pin
the publish target, without changing code (all keys optional):

```json
{
  "disabled_test_ids": ["PRODUCTPREFIX-SEM-008"],
  "disabled_scanners": ["VIEW", "TEXT"],
  "publish_trust_table": "ProductPrefix_OBS_STD_T.trust_engine_run"
}
```

Keep rule config files under `config/`, for example copy `config/rules.example.json` to
`config/rules.json`, then pass it to `generate-tests` or `validate`:

```powershell
python -m ai_native_data_product_trust_engine validate --prefix ProductPrefix --rules-config config\rules.json
```

Scanner names are:

- `CAPABILITY`: checks whether metadata and recipes only claim platform features that the deployed
  product actually exposes.
- `QUERY`: validates active cookbook SQL templates, bounded-query safeguards, parameters and
  EXPLAIN readiness.
- `RELATIONSHIP`: samples declared relationship keys for orphan evidence, cardinality mismatches
  and temporal current-record contract issues.
- `TEXT`: checks glossary text, cookbook notes and metadata descriptions for stale object names,
  aliases and free-text references.
- `VIEW`: validates standard view contracts, business-view source layering, locking access patterns
  and view compile/readiness checks.

Disabled checks and scanner families are still shown in the JSON and HTML reports under
`excluded_checks`, so reviewers can distinguish "passed" from "not run".

Module deployment scope comes from `{Product}_SEM_STD_V.data_product_map`. A module is in scope
only when `COALESCE(is_active, 1) = 1` and `deployment_status = 'DEPLOYED'`. To keep a module in the
catalogue but exclude it from module-owned object checks, set `deployment_status` to a non-deployed
state such as `NOT_DEPLOYED` or set `is_active = 0`. Query cookbook rows remain governed by their own
`is_active` metadata because a recipe can intentionally be unavailable even when its module exists.

## Agent-Friendly MCP Orientation Layer

The Trust Engine can expose local report evidence through an optional MCP server so agents do not
have to scrape HTML or guess where to start. Install the optional MCP extra, run validation to write
JSON reports, then start the server over the report directory:

```powershell
pip install .[mcp]
python -m ai_native_data_product_trust_engine validate --prefix ProductPrefix --output reports\productprefix-validation.json
python -m ai_native_data_product_trust_engine mcp-server --reports-dir reports
```

The first resource is `trust://products`. From there, agents read the product orientation manifest
before inspecting details:

- `trust://products/{prefix}/orientation`
- `trust://products/{prefix}/latest-report`
- `trust://products/{prefix}/scores`
- `trust://products/{prefix}/checks`
- `trust://products/{prefix}/failures`
- `trust://products/{prefix}/repair-candidates`

The MCP tools follow the same metadata-first handshake: `search_data_products`,
`describe_data_product`, `get_recommended_entrypoint`, `list_failed_checks`,
`generate_repair_plan` and `explain_check`. These are deliberately report-backed and read-only in
this slice. Agents get a safe map of trust state, failure consequences and repair posture before any
data product access path is considered.

## First Working Slice

The first implemented slice generates and executes metadata trust tests including:

- Entity metadata references deployed objects.
- Column metadata references deployed columns.
- Relationship metadata references deployed join columns.
- Relationship join columns have compatible datatype, length, precision, scale and character set.
- Same/similar table column names have consistent datatype, length, precision and scale.
- Column metadata datatype declarations and coverage stay current with deployed entity columns.
- Data product primary views, entity view names, relationship endpoints and lineage endpoints use
  deployed BUS_V access-layer objects for governed agent access.
- `DataProductsMaster_GOV_BUS_V.active_data_product_registry` exists for product-first MCP discovery.
- Data product registry and orientation manifest match deployed metadata.
- Active cookbook recipes exist for later SQL template validation.
- Product tables stay within the initial AMP storage skew warning threshold.
- Relationship join columns have valid optimiser statistics.

The validator supports `ZERO_ROWS` and `NON_EMPTY` expectations, records pass/fail/error
evidence, and returns a non-zero exit code when any generated test fails.

Table column type consistency validation normalises column names by case and underscores, then
flags normalised names with multiple physical type signatures across deployed product tables. This
catches join-risk patterns such as the same business key being defined with different datatypes,
lengths, precision or scale in different table modules. View output column metadata is resolved
through `HELP COLUMN`, not `DBC.ColumnsV`, because Teradata does not expose view datatypes through
the table column catalogue in the same way.

Relationship join column compatibility validation checks active `table_relationship` rows against
`DBC.ColumnsV` for both sides of each declared join. It reports type, length, precision, scale and
character-set mismatches with source and target column evidence, so stewards can align the physical
join key or expose a compatible view-layer key before agents generate SQL from the relationship.

Relationship health validation runs bounded evidence checks over active relationship metadata. It
samples declared source and target keys to report source-to-target and target-to-source orphan
rates, validates whether observed duplicate key behaviour contradicts declared `1:1`, `1:M` or
`M:1` cardinality, and checks temporal entities for duplicate current rows and current-state views
that omit the declared current flag filter.

The first free-text validation primitive is also in place. It scans entity, column,
relationship, cookbook and glossary metadata text for known retired aliases and typo suspects such
as `v_relationship_paths` and `v_relationship_patsh`, then reports table, column, row key, token,
replacement and safe-auto eligibility.

Query template validation now fetches active `Query_Cookbook` rows, binds named parameters with
deterministic validation literals, runs `EXPLAIN`, and reports one result per recipe. Initial failure
classes include missing columns, missing objects, unsupported functions, unsupported native
capabilities and syntax errors. Query failures include extracted object/column/function names where
available plus a first repair hint.

Interactive recipe bounds validation treats active cookbook entries as agent-facing unless their
title, use case, performance notes or complexity clearly identify them as batch, exhaustive,
offline, training or full-extract patterns. Agent-facing recipes must include a parameterised
predicate or row-limiting construct such as `TOP`, `SAMPLE`, `QUALIFY ROW_NUMBER` or `FETCH FIRST`.

EXPLAIN performance validation scans recipe plans for early risk signals including product joins,
all-AMP scans, duplicated large table access, missing or stale statistics, and low-confidence
estimates. These checks feed the performance readiness score rather than the data product trust
score, because a product can be semantically trustworthy while still needing access-path tuning.

Statistics coverage validation checks active relationship join columns against `DBC.ColumnStatsV`.
Missing valid statistics are reported as performance readiness warnings with the relationship name,
join-column usage, issue code and a `COLLECT STATISTICS` repair hint. This keeps the check focused on
metadata-backed access paths that agents are likely to use for generated joins.

Primary index health validation checks product tables for missing primary index definitions, nullable
PI columns, low-cardinality-looking PI column names and observed high AMP storage skew. These are
warning-level structural findings because some NoPI or skewed designs can be intentional, but the
report includes table size, skew, PI columns and repair guidance so agents can distinguish documented
design choices from likely distribution defects.

Operational readiness now has an initial evidence baseline instead of remaining unassessed. The
first checks verify that the Observability module is registered and deployed, and that required
operational evidence objects are present: `change_event`, `data_quality_metric`, `data_lineage`,
`lineage_run`, plus the Semantic `lineage_graph` and `lineage_run_latest` views. These checks make
the operational readiness score reflect whether freshness, lineage, quality and usage evidence can
be captured and inspected.

View contract validation discovers deployed product views from `DBC.TablesV` and runs `HELP COLUMN`
against an aliased zero-row subquery for each view:
`HELP COLUMN dt01.* FROM (SELECT viw.* FROM <database>.<view> AS viw WHERE 1=2) AS dt01`.
Teradata resolves the view and returns authoritative output-column metadata, validating that source
objects, projected columns, join columns, aliases and predicates still compile without scanning
business data.

Standard view-layer validation enforces `%_STD_V` as a thin 1:1 agent contract over `%_STD_T`
tables. A standard view must declare its view column list before `AS`, use `LOCKING ROW FOR ACCESS`,
avoid `SELECT *`, avoid predicates, joins, aggregations and transformations, and project columns in
the same `ColumnId` order as the matching table. Business logic belongs in `%_BUS_V` views, and
those business views must select from `%_STD_V` access views rather than directly from `%_STD_T`
tables.

Standard view column-contract validation separately compares every `%_STD_V` view column list to the
matching `%_STD_T` table by `ColumnId`, so missing, extra or reordered columns are reported as a
specific structural failure.

Standard table/view coverage validation checks every `%_STD_T` table has a same-named `%_STD_V`
locking access view. Missing views are reported as structural contract failures so agents and
applications do not have to query product tables directly.

View locking validation checks every product view that directly queries a table, even outside the
preferred layer architecture. `LOCKING ROW FOR ACCESS` is preferred. `LOCKING TABLE <table> FOR
ACCESS` is accepted when it names the directly referenced table, but is treated as more fragile
because every source table must be named correctly.

The first capability registry primitive discovers native VECTOR and fallback embedding evidence
from deployed objects. It reports capability inventory and fails alignment when active cookbook
metadata references native VECTOR behaviour while the product only has fallback embedding evidence.
Semantic-search capability alignment also scans cookbook, entity, column, relationship and glossary
text for semantic search, embedding, cosine similarity, nearest-neighbour and native VECTOR claims.
Semantic-search claims are accepted when either native VECTOR or fallback embedding evidence exists,
but native VECTOR wording requires native VECTOR evidence. Findings include the source table, column,
row key and a repair hint so agents can update wording or route users to the supported pattern.

Repair proposal mode generates Markdown and SQL repair artifacts beside the validation report. Safe
auto mode applies only deterministic repairs that do not require steward approval, such as known
free-text alias replacements, and reports permission failures without hiding the remaining
validation evidence. For temporal metadata tables such as `Query_Cookbook`, generated repairs
expire the current row and insert a corrected successor row rather than mutating the current record
in place.

The optional HTML report is the human-facing companion to the JSON evidence. It uses Teradata brand
colours, separate data product trust, performance readiness and operational readiness scorecards,
tabbed sections, filterable validation results, repair posture summaries and embedded structured
evidence so users can scan the current trust position and decide what to fix next. Hover text and the
glossary explain terms such as structural, semantic, query, capability, free-text, performance,
operational and repairs.
Failure rows show a concise backend error and a suggested next step first; raw stack traces remain in
the structured JSON evidence for agents and deeper diagnostics.
When multiple validation failures share the same missing column, the report correlates them and
lists the affected product views or other dependent objects to recreate/test first. This lets users
move from a broken recipe to the likely impacted view-layer contract without reading raw SQL or
driver stack traces.

To quarantine a broken recipe, retire the active `Query_Cookbook` row rather than deleting it:
set `is_active = 0`, close `valid_to`, and preserve the row for audit/history. A corrected successor
recipe can then be inserted when the SQL contract is repaired. Quarantined recipes stay visible in
history but are excluded from active recipe validation and agent selection.

Table skew validation alerts on product tables whose per-AMP storage distribution is materially
uneven. The generated check uses `DBC.TableSizeV` per-AMP `CurrentPerm` evidence rather than hashing
`DBC.TablesV.TableName`, because the latter measures dictionary rows, not the data product table's
distribution. Initial results use a warning threshold of `skew_percent > 20` so stewards can review
primary index choice, data distribution and statistics without treating every skewed table as a
blocking metadata defect.

## Repository Status

This repository is currently in early implementation. The first working slice generates metadata
consistency tests from a product prefix and runs them against Teradata using an injected database
adapter.
