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
```

Live validation currently uses `DATABASE_URI` by default and writes JSON validation evidence.
Generated reports are local artifacts and are not committed.

## First Working Slice

The first implemented slice generates and executes four metadata trust tests:

- Entity metadata references deployed objects.
- Column metadata references deployed columns.
- Relationship metadata references deployed join columns.
- Active cookbook recipes exist for later SQL template validation.

The validator supports `ZERO_ROWS` and `NON_EMPTY` expectations, records pass/fail/error
evidence, and returns a non-zero exit code when any generated test fails.

The first free-text validation primitive is also in place. It scans entity, column,
relationship, cookbook and glossary metadata text for known retired aliases and typo suspects such
as `v_relationship_paths` and `v_relationship_patsh`, then reports table, column, row key, token,
replacement and safe-auto eligibility.

Query template validation now fetches active `Query_Cookbook` rows, binds named parameters with
deterministic validation literals, runs `EXPLAIN`, and reports one result per recipe. Initial failure
classes include missing columns, missing objects, unsupported functions, unsupported native
capabilities and syntax errors. Query failures include extracted object/column/function names where
available plus a first repair hint.

The first capability registry primitive discovers native VECTOR and fallback embedding evidence
from deployed objects. It reports capability inventory and fails alignment when active cookbook
metadata references native VECTOR behaviour while the product only has fallback embedding evidence.

## Repository Status

This repository is currently in early implementation. The first working slice generates metadata
consistency tests from a product prefix and runs them against Teradata using an injected database
adapter.
