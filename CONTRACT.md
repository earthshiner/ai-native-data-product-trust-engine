# Trust payload contract

The Trust Engine (**producer**) publishes a `trust_engine_latest` row; the
[Data Product Browser](https://github.com/earthshiner/data-product-browser)
(**consumer**) reads it and renders the verdict, the failed-check breakdown and
the "Path to trusted" advisory. Neither repo imports the other — they are
coupled **only** through this wire format. Per **ADR-0001**, trust is computed
solely here; the Browser is read-only and must never re-derive a trust verdict.

This document pins the format so the two repos cannot drift silently. It is
enforced by `tests/test_contract.py` (producer) and the Browser's
`tests/test_trust_contract.py` (consumer), both of which run against the shared
golden fixture `contract/trust_payload_example.json` (generated from the real
serialiser — do not hand-edit).

**Schema version: `2.0`** (`contract.PAYLOAD_SCHEMA_VERSION`).

**2.0 change:** `started_at` / `completed_at` (`VARCHAR(40)` ISO-8601 strings)
became `started_dts` / `completed_dts` (`TIMESTAMP(6) WITH TIME ZONE`) —
canonical temporal names and types per the Temporal & Lifecycle Metadata
Standard. Latest-run selection now orders on the typed column, so it is
chronological by construction (under 1.x, lexicographic ordering silently
mis-selected the latest run if any row carried a non-UTC offset). The JSON
fixture carries the timestamps as canonical ISO-8601 strings (JSON has no
timestamp type); a live `SELECT` returns typed values, which consumers already
parse as datetimes. Consumers still bound to 1.x names can read a projection
aliasing `started_dts AS started_at`.

## Row columns — `<prefix>_SEM_BUS_V.trust_engine_latest`

Source of truth: `trust_publish._PUBLISH_COLUMNS` / `trust_table_ddl()`.

| Column | Type | Notes |
|---|---|---|
| `product_prefix` | VARCHAR(128) | Data product prefix, e.g. `CallCentre` |
| `run_id` | VARCHAR(64) | Stable hash of prefix + the canonical ISO timestamp strings + check count |
| `started_dts` / `completed_dts` | TIMESTAMP(6) WITH TIME ZONE | Run instants; UTC persistence |
| `trust_status` | VARCHAR(16) | `TRUSTED` \| `DEGRADED` \| `UNTRUSTED` |
| `agent_use_allowed` | BYTEINT | 1 when status ∈ {TRUSTED, DEGRADED} |
| `total_checks` / `passed_count` / `failed_count` / `error_count` | INTEGER | |
| `critical_failure_count` / `error_failure_count` | INTEGER | **Authoritative** severity gate counts — always trust these over the (capped) blob below |
| `data_product_trust_score` / `performance_readiness_score` / `operational_readiness_score` | INTEGER | 0–100, nullable |
| `repair_candidate_count` | INTEGER | True total (blob is capped) |
| `failed_checks_json` | JSON(32000) | See below — **capped at 20 checks** |
| `repair_candidates_json` | JSON(32000) | See below — **capped at 20 candidates** |

## `failed_checks_json` — array of failed/error checks (≤ 20)

Each item: `test_id`, `name`, `category`, `severity` (`CRITICAL`|`ERROR`|`WARNING`),
`status` (`FAILED`|`ERROR`), `row_count` (true total for that check), `sample_rows`
(**≤ 3**, see below), `error_message` (nullable), `repair_strategy`.

Consumers must treat `row_count` as the true count and `sample_rows` as at most
the first 3 examples (surface a "+N more" from `row_count - shown`).

## `repair_candidates_json` — array of proposals (≤ 20)

Each item: `candidate_id`, `issue_code`, `summary`, `mode` (currently always
`proposal`), `requires_approval`, `sql`.

## `sample_rows` key catalogue

`sample_rows` is **untyped and per-check** — keys vary by `issue_code`. Every
row carries `issue_code` + `repair_hint`. The object-identifying keys a consumer
should read to answer "which objects?" are, per issue_code:

| issue_code | object-identifying keys | other keys |
|---|---|---|
| `ENTITY_VIEW_NAME_NOT_DEPLOYED` / `ENTITY_VIEW_NAME_MISSING` | `entity_name`, `view_name`, `business_database_name` | `entity_metadata_id` |
| `CURRENT_VIEW_NOT_DEPLOYED` / `CURRENT_VIEW_NOT_DECLARED` | `entity_name`, `view_name` | |
| `MISSING_ORIENTATION_MANIFEST` | `product_id` | `issue_detail` |
| `DATA_PRODUCT_MAP_PRIMARY_VIEWS_MISSING` | `object_name`, `database_name`, `column_name` | `issue_detail` |
| `LINEAGE_VIEW_NOT_DEPLOYED` / `MISSING_OBSERVABILITY_BUS_VIEW` | `object_name`, `database_name` | `issue_detail` |
| `MISSING_OBSERVABILITY_TABLE` / `MISSING_OBSERVABILITY_SEMAN` | `object_name`, `observability_database` | `issue_detail` |
| `UNBOUNDED_INTERACTIVE_RECIPE` | `recipe_id`, `recipe_title` | `interactive_recipe`, `missing_bound_type`, `parameters`, `source_module`, `validation_mode` |
| `EXPLAIN_*` (`ALL_AMP_SCAN`, `LOW_CONFIDENCE`, `DUPLICATED_LARGE_TABLE`, `PRODUCT_JOIN`) | `recipe_id`, `recipe_title`, `referenced_objects` | `finding`, `helpstats_enabled` |
| `UNSUPPORTED_CAPABILITY` | `recipe_id`, `recipe_title`, `source_table` | `capability`, `unsupported_feature`, `source_column`, `row_key` |
| `COLUMN_TYPE_DRIFT` | `database_name`, `table_name`, `column_name` | `type_signature`, `normalised_column_name`, `column_type`, `column_length`, `decimal_*` |
| `COLUMN_METADATA_DATATYPE_MISMATCH` | `database_name`, `table_name`, `column_name` | `deployed_column_type`, `metadata_data_type` |
| `MISSING_COLUMN_METADATA` | `database_name`, `table_name`, `column_name` | |
| `MISSING_JOIN_COLUMN_STATS` | `database_name`, `table_name`, `column_name`, `relationship_name` | `usage_type` |
| `STD_VIEW_COLUMN_ORDER_MISMATCH` | `database_name`, `view_name`, `table_column_name`, `view_column_name` | `base_database_name`, `base_table_name`, `column_id` |
| `TABLE_AMP_SKEW` / `PRIMARY_INDEX_SKEW_HIGH` | `database_name`, `table_name` | `skew_percent`, `*_amp_perm_bytes`, `primary_index_columns`, `total_perm_bytes` |
| `SOURCE_TO_TARGET_ORPHAN` / `TARGET_TO_SOURCE_ORPHAN` | `relationship_name` | `affected_side`, `orphan_count`, `orphan_rate_percent`, `sample_count` |
| `DUPLICATE_CURRENT_RECORD` | `entity_name` | `natural_key`, `current_row_count` |
| `BUS_VIEW_SELECTS_TABLE_DIRECTLY` / `MISSING_LOCKING_ROW` / `DIRECT_TABLE_VIEW_MISSING_LOCK` | `database_name`, `view_name` | `referenced_table`, `evidence` |

**When you add a new check with new `sample_rows` keys:** if it introduces a new
object-identifying key, add it to the Browser's `offenderLabels()` vocabulary
(`app.js`) and to this table, and bump `PAYLOAD_SCHEMA_VERSION` if the change is
incompatible. The Browser's contract test asserts every `sample_row` in the
golden fixture carries at least one key it knows how to render.

## Changing the contract

1. Change the serialiser / add the check.
2. Regenerate the golden: `uv run python -c "import json; from ai_native_data_product_trust_engine.contract import contract_fixture; fh = open('contract/trust_payload_example.json','w',encoding='utf-8',newline='\n'); json.dump(contract_fixture(), fh, indent=2, sort_keys=True, ensure_ascii=False); fh.write('\n')"`
3. Bump `PAYLOAD_SCHEMA_VERSION` if incompatible.
4. **Re-vendor** `contract/trust_payload_example.json` into the Browser at
   `tests/fixtures/trust_payload_example.json` and update its expected version.

## Deferred: runtime version detection

A `payload_schema_version` **column** on `trust_engine_run` would let the Browser
detect drift at runtime (not just at build time). That is a coordinated DDL
migration on a live shared table, so it is intentionally **not** implemented here
— track it as a follow-up when a migration window is available. Until then, the
version lives in the golden fixture wrapper and is enforced at build time by both
repos' contract tests.
