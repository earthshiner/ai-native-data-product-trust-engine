# Product Narrative

## Problem

AI-native Data Products depend on metadata becoming executable truth. Agents, notebooks, generated
SQL, semantic search, cookbooks and application builders all use metadata to decide which objects
exist, how they join, which capabilities are available, what business terms mean and which query
patterns are safe.

When metadata drifts, the failure mode is uncomfortable: AI systems can produce confident but
wrong outputs. A stale object name in a description, a broken relationship row, a 1:1 view that no
longer exposes an underlying column, or a recipe that advertises native VECTOR support before the
product has VECTOR columns can all cause downstream generated assets to fail.

## Positioning

The AI-Native Data Product Trust Engine validates the metadata contract of a deployed data product.
It is not a general-purpose data quality scorer. It focuses on whether the metadata is accurate,
current, executable and capability-aligned enough for AI systems to safely consume.

## Metadata Trust Versus Data Quality

Metadata trust asks:

- Does `entity_metadata` point to real deployed objects?
- Does `column_metadata` point to real deployed columns?
- Do relationship definitions reference compatible join columns?
- Do relationship paths produce executable SQL?
- Do SQL recipes bind, explain and return the expected shape?
- Do free-text metadata fields reference current object names rather than retired aliases?
- Do recipe descriptions and SQL templates match the capabilities actually deployed?

Data quality asks broader questions:

- Are source values correct?
- Are business events complete?
- Are operational measurements accurate?
- Are KPI definitions appropriate?

The Trust Engine may use narrow data checks where they validate metadata. For example, an orphan
rate test can prove whether a mandatory relationship is trustworthy. A current-record uniqueness
test can prove whether current-state metadata is safe to use. These are evidence checks in service
of metadata trust.

## Why It Matters

Without metadata trust, every generated experience inherits hidden risk:

- Cookbooks can publish broken SQL.
- Agents can choose invalid join paths.
- Semantic search can point to unsupported embedding patterns.
- Demos can present stale architecture claims.
- Applications can depend on columns that a view no longer exposes.

The Trust Engine gives the data product a way to prove its own contract continuously, classify drift,
and create auditable repair proposals before those issues reach users.

## Example Failure Classes

- `STALE_OBJECT_NAME`: free text references `v_relationship_paths` after the object was renamed to
  `relationship_paths`.
- `TYPO_SUSPECT`: free text references `v_relationship_patsh`.
- `BROKEN_RELATIONSHIP`: `table_relationship` references a source or target column that no longer
  exists.
- `VIEW_DRIFT`: a 1:1 view no longer exposes the columns promised by its source contract.
- `UNSUPPORTED_CAPABILITY`: metadata or SQL advertises `TD_VECTORDISTANCE` before native VECTOR
  support is deployed.
- `STALE_RECIPE`: `Query_Cookbook.sql_template` no longer parses, binds or explains.

## Product Promise

The Trust Engine turns metadata from passive documentation into a tested, scored and repairable
contract. It lets AI-native Data Products say:

> Here is what I claim. Here is the evidence that those claims still hold. Here are the repairs
> needed where they do not.
