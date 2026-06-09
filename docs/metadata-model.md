# Metadata Model

The first metadata extension should live in the Memory and Observability modules.

## Memory Module

- Trust_Test_Case
- Trust_Validation_Run
- Trust_Validation_Result
- Trust_Repair_Candidate
- Product_Capability
- Metadata_Migration_Rule

## Observability Module

- Trust_Score_History
- Contract_Drift_Event
- Metadata_Evidence_Observation

## Required Statuses

- VALIDATED
- VALIDATED_WITH_WARNING
- FAILED
- REPAIRED
- QUARANTINED
- GENERATED_NOT_VALIDATED

## Scope Note

The metadata model records evidence about the correctness of the data product contract. It should
avoid broad raw-data profiling tables unless the observations directly support metadata trust, such
as relationship validity, current-record semantics, allowed values or required reference
populations.

## Data Product Object Mapping

`data_product_map.database_name` cannot identify the database for both `primary_tables` and
`primary_views` when a module stores physical tables and access views in different databases.
Comma-separated object lists also prevent database-level referential validation.

The compatible short-term contract allows `primary_views` entries to use either `view_name` or
`database_name.view_name`. A future breaking migration should replace `primary_tables` and
`primary_views` with a child table containing one row per object:

- `module_id`
- `object_database_name`
- `object_name`
- `object_type`
- `object_role`
- `is_primary`
- `is_active`

This keeps module metadata in `data_product_map` while giving every mapped object its own database,
type and lifecycle state.
