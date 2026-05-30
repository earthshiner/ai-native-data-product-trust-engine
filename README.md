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
- Publish a trust score that can be consumed by agents and user-facing assets.

## Trust Model

The engine validates five contract areas:

1. Structural contract: databases, tables, views, columns, datatypes, and view drift.
2. Semantic contract: entities, column metadata, relationships, paths, glossary and design metadata.
3. Query contract: parameter binding, SQL parsing, EXPLAIN validation, smoke execution and expected result shape.
4. Capability contract: VECTOR, JSON, geospatial, ML, fallback patterns and product feature flags.
5. Evidence checks: targeted row-count, orphan, uniqueness, current-record and category checks
   only where they validate a metadata claim.

## Self-Healing Levels

- Level 0: detect only.
- Level 1: propose repair.
- Level 2: safe metadata repair.
- Level 3: safe generated-object regeneration.
- Level 4: human-approved structural change.

The first implementation will default to proposal mode. Automatic repair is only allowed when the fix is deterministic, idempotent, and auditable.

## Initial CLI Shape

```powershell
python -m ai_native_data_product_trust_engine discover --prefix CallCentre
python -m ai_native_data_product_trust_engine generate-tests --prefix CallCentre
python -m ai_native_data_product_trust_engine validate --prefix CallCentre --repair-mode proposal
python -m ai_native_data_product_trust_engine report --prefix CallCentre
```

During local development, run from the repository root with `src` on `PYTHONPATH`:

```powershell
$env:PYTHONPATH='src'
python -m ai_native_data_product_trust_engine generate-tests --prefix CallCentre
python -m ai_native_data_product_trust_engine validate --prefix CallCentre --output reports\callcentre-validation.json
python -m ai_native_data_product_trust_engine validate --prefix CallCentre --output reports\callcentre-validation.json --html-output reports\callcentre-validation.html
```

Live validation currently uses `DATABASE_URI` by default and writes JSON validation evidence. Use
`--html-output` to also create a standalone interactive HTML report for human review. Generated
reports are local artifacts and are not committed.

## First Working Slice

The first implemented slice generates and executes seven metadata trust tests:

- Entity metadata references deployed objects.
- Column metadata references deployed columns.
- Relationship metadata references deployed join columns.
- Same/similar column names have consistent datatype, length, precision and scale.
- Active cookbook recipes exist for later SQL template validation.
- Product tables stay within the initial AMP storage skew warning threshold.
- Relationship join columns have valid optimiser statistics.

The validator supports `ZERO_ROWS` and `NON_EMPTY` expectations, records pass/fail/error
evidence, and returns a non-zero exit code when any generated test fails.

Column type consistency validation normalises column names by case and underscores, then flags
normalised names with multiple physical type signatures across the data product. This catches
join-risk patterns such as the same business key being defined with different datatypes, lengths,
precision or scale in different modules or views.

The first free-text validation primitive is also in place. It scans entity, column,
relationship, cookbook and glossary metadata text for known retired aliases and typo suspects such
as `v_relationship_paths` and `v_relationship_patsh`, then reports table, column, row key, token,
replacement and safe-auto eligibility.

Query template validation now fetches active `Query_Cookbook` rows, binds named parameters with
deterministic validation literals, runs `EXPLAIN`, and reports one result per recipe. Initial failure
classes include missing columns, missing objects, unsupported functions, unsupported native
capabilities and syntax errors. Query failures include extracted object/column/function names where
available plus a first repair hint.

Statistics coverage validation checks active relationship join columns against `DBC.ColumnStatsV`.
Missing valid statistics are reported as performance trust warnings with the relationship name,
join-column usage, issue code and a `COLLECT STATISTICS` repair hint. This keeps the check focused on
metadata-backed access paths that agents are likely to use for generated joins.

View contract validation discovers deployed product views from `DBC.TablesV` and runs `HELP COLUMN`
against a zero-row subquery for each view. Teradata resolves the view and returns authoritative
output-column metadata, validating that source objects, projected columns, join columns, aliases and
predicates still compile without scanning business data.

Standard view-layer validation enforces `%_STD_V` as a thin 1:1 agent contract over `%_STD_T`
tables. A standard view must declare its view column list before `AS`, use `LOCKING ROW FOR ACCESS`,
avoid `SELECT *`, avoid predicates, joins, aggregations and transformations, and project columns in
the same `ColumnId` order as the matching table. Business logic belongs in `%_BUS_V` views, and
those business views must select from `%_STD_V` access views rather than directly from `%_STD_T`
tables.

View locking validation checks every product view that directly queries a table, even outside the
preferred layer architecture. `LOCKING ROW FOR ACCESS` is preferred. `LOCKING TABLE <table> FOR
ACCESS` is accepted when it names the directly referenced table, but is treated as more fragile
because every source table must be named correctly.

The first capability registry primitive discovers native VECTOR and fallback embedding evidence
from deployed objects. It reports capability inventory and fails alignment when active cookbook
metadata references native VECTOR behaviour while the product only has fallback embedding evidence.

Repair proposal mode generates Markdown and SQL repair artifacts beside the validation report. Safe
auto mode applies only deterministic repairs that do not require steward approval, such as known
free-text alias replacements, and reports permission failures without hiding the remaining
validation evidence. For temporal metadata tables such as `Query_Cookbook`, generated repairs
expire the current row and insert a corrected successor row rather than mutating the current record
in place.

The optional HTML report is the human-facing companion to the JSON evidence. It uses Teradata brand
colours, concise scorecards, filterable validation results, repair posture summaries and embedded
structured evidence so users can scan the current trust position and decide what to fix next.
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
