# Test Taxonomy

## Structural Tests

- Database exists.
- Object exists.
- Column exists.
- View exposes declared contract.
- Datatype matches metadata.
- Join key datatype compatibility holds.

## Semantic Tests

- Entity metadata points to real objects.
- Column metadata points to real columns.
- Relationship metadata points to real join columns.
- Relationship paths are executable.
- Glossary terms reference valid objects.

## Query Tests

- Recipe SQL has all required parameters declared.
- Parameters can be bound from sample values.
- EXPLAIN succeeds.
- Smoke execution returns expected columns.
- Recipes avoid unsupported product capabilities.

## Capability Tests

- Native VECTOR capability is present before VECTOR recipes are enabled.
- Fallback embedding capability is present when native VECTOR is absent.
- JSON, geospatial, temporal and ML features are validated before use.

## Data Quality Tests

- Required row counts are non-zero.
- Primary business keys are unique.
- Mandatory relationships do not orphan above tolerance.
- Current-record filters return one current row per key.
- Allowed categorical values are respected.
