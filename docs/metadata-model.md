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
