"""Producer-side guard for the published trust-payload contract (CONTRACT.md).

Fails loudly when the serialiser changes without the golden fixture being
regenerated, so drift with the Data Product Browser can't slip through.
"""

import json
from pathlib import Path

from ai_native_data_product_trust_engine.contract import (
    PAYLOAD_SCHEMA_VERSION,
    contract_fixture,
)
from ai_native_data_product_trust_engine.trust_publish import (
    _AREA_COLUMNS,
    _PUBLISH_COLUMNS,
    _RUN_COLUMNS,
)

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "contract" / "trust_payload_example.json"

_FAILED_CHECK_KEYS = {
    "test_id",
    "name",
    "category",
    "severity",
    "status",
    "scope_kind",
    "scope_id",
    "row_count",
    "sample_rows",
    "error_message",
    "repair_strategy",
}
_REPAIR_KEYS = {"candidate_id", "issue_code", "summary", "mode", "requires_approval", "sql"}


def _golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_golden_fixture_matches_serialiser():
    # If this fails, regenerate contract/trust_payload_example.json (see CONTRACT.md)
    # and re-vendor it into the Browser — the serialiser changed.
    assert _golden() == contract_fixture()


def test_golden_declares_current_schema_version():
    assert _golden()["payload_schema_version"] == PAYLOAD_SCHEMA_VERSION


def test_row_columns_match_publish_columns():
    row = _golden()["trust_engine_latest"]
    assert set(row) == set(_PUBLISH_COLUMNS)


def test_failed_checks_blob_shape_and_cap():
    row = _golden()["trust_engine_latest"]
    checks = json.loads(row["failed_checks_json"])
    assert len(checks) <= 20
    for check in checks:
        assert _FAILED_CHECK_KEYS.issuperset(check), f"unexpected keys in {check}"
        assert {"test_id", "severity", "repair_strategy", "sample_rows"}.issubset(check)
        assert len(check["sample_rows"]) <= 3
        for sample in check["sample_rows"]:
            assert "issue_code" in sample


def test_repair_candidates_blob_shape_and_cap():
    row = _golden()["trust_engine_latest"]
    repairs = json.loads(row["repair_candidates_json"])
    assert len(repairs) <= 20
    for repair in repairs:
        assert set(repair) == _REPAIR_KEYS


def test_validation_run_row_matches_run_columns_and_declares_identity():
    row = _golden()["validation_run"]
    assert set(row) == set(_RUN_COLUMNS)
    assert row["payload_schema_version"] == PAYLOAD_SCHEMA_VERSION
    assert row["producer_id"] and row["source_format"] == "NATIVE"
    assert row["agent_use_allowed"] == 1  # deprecated at 2.1: never a decision


def test_validation_areas_match_columns_and_vocabularies():
    areas = _golden()["validation_area"]
    assert areas
    run_id = _golden()["validation_run"]["run_id"]
    for area in areas:
        assert set(area) == set(_AREA_COLUMNS)
        assert area["run_id"] == run_id
        assert area["scope_kind"] in {"MODULE", "ENTITY", "PATTERN", "CAPABILITY", "PRODUCT"}
        assert area["area_status"] in {"pass", "fail", "partial", "not-validated", "no-evidence"}
        assert area["confidence"] in {"strong", "partial", "weak", "unknown"}
        if area["confidence"] != "strong":
            assert area["open_gaps"] and area["recommended_action"]


def test_every_failed_check_scope_resolves_to_an_area_in_the_same_run():
    areas = {(a["scope_kind"], a["scope_id"]) for a in _golden()["validation_area"]}
    checks = json.loads(_golden()["trust_engine_latest"]["failed_checks_json"])
    for check in checks:
        assert (check["scope_kind"], check["scope_id"]) in areas


def test_golden_covers_an_uncovered_area_as_no_evidence():
    areas = _golden()["validation_area"]
    assert any(a["area_status"] == "no-evidence" and a["confidence"] == "unknown" for a in areas)
