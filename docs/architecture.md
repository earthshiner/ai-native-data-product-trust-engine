# Architecture

The Trust Engine has four separable layers.

## Discovery Layer

Discovers product modules, semantic metadata, memory metadata, physical objects, capabilities and lineage sources from Teradata.

## Contract Layer

Builds an in-memory product contract from discovered metadata. The contract is the source for generated validation tests and repair candidates.

## Execution Layer

Runs generated tests through an injected database adapter. Validation must support dry-run, EXPLAIN-only, smoke execution and full execution modes.

## Repair Layer

Classifies failures into deterministic safe repairs, repair proposals, or manual steward tasks. Every repair must be auditable and idempotent.

## Reporting Layer

Publishes trust scores, issue lists, validation evidence and repair history for MCP tools, cookbooks, dashboards and observability modules.
