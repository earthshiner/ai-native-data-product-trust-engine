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
from ai_native_data_product_trust_engine.trust_publish import _PUBLISH_COLUMNS

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "contract" / "trust_payload_example.json"

_FAILED_CHECK_KEYS = {
    "test_id",
    "name",
    "category",
    "severity",
    "status",
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
