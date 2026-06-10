# PR: Retire SEM-009 ENTITY_DELETED_FLAG rule

## Summary
SEM-009 ("Entity deleted flag metadata is populated and deployed") required every active SCD2 entity to declare and deploy an `is_deleted` column via `entity_metadata.deleted_flag_column`. In practice it duplicates the product-wide `is_active` soft-delete convention and fires false positives on entities that legitimately don't track deletes (e.g. derived feature stores: `call_behaviour_features`, `model_prediction`).

## Changes
- **`src/.../test_generation.py`** — removed the SEM-009 `TestCase` block; left a comment explaining the retirement. SEM-010 and SEM-011 keep their numbers so historical trust-engine reports remain valid.
- **`tests/test_generation.py`** — dropped SEM-009 from the three id-list assertions, replaced the SEM-009 detail-assertion block with a "not generated" check, and bumped the `tests[14]` → `tests[13]` index assertion to account for the removal.

## Test plan
- [x] `uv run pytest -q` — 103 passed.
- [ ] Re-run trust engine against CallCentre — CALLCENTRE-SEM-009 no longer appears in the failed-checks list.
