import json

import pytest

from ai_native_data_product_trust_engine.cli import main
from ai_native_data_product_trust_engine.mcp_server import (
    build_check_explanation,
    build_failures_resource,
    build_orientation_resource,
    build_repair_candidates_resource,
    discover_products_payload,
    load_latest_report,
)


def test_mcp_server_cli_starts_server_over_report_directory(monkeypatch, tmp_path):
    seen = {}

    def fake_run_mcp_server(reports_dir):
        seen["reports_dir"] = reports_dir

    monkeypatch.setattr(
        "ai_native_data_product_trust_engine.mcp_server.run_mcp_server",
        fake_run_mcp_server,
    )

    exit_code = main(["mcp-server", "--reports-dir", str(tmp_path)])

    assert exit_code == 0
    assert seen["reports_dir"] == tmp_path


def test_discover_products_payload_lists_orientation_entrypoint(tmp_path):
    report_path = tmp_path / "callcentre-validation.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")

    payload = discover_products_payload(tmp_path)

    assert payload["resource"] == "trust://products"
    assert payload["products"] == [
        {
            "prefix": "CallCentre",
            "orientation": "trust://products/CallCentre/orientation",
            "latest_report": "trust://products/CallCentre/latest-report",
            "completed_at": "2026-06-01T10:01:30+10:00",
            "summary": _report()["summary"],
            "scores": _report()["scores"],
        }
    ]


def test_load_latest_report_chooses_newest_report_for_prefix(tmp_path):
    old_report = _report(completed_at="2026-05-30T10:01:30+10:00")
    newest_report = _report(completed_at="2026-06-01T10:01:30+10:00")
    (tmp_path / "old.json").write_text(json.dumps(old_report), encoding="utf-8")
    (tmp_path / "new.json").write_text(json.dumps(newest_report), encoding="utf-8")

    report = load_latest_report("callcentre", tmp_path)

    assert report["completed_at"] == "2026-06-01T10:01:30+10:00"


def test_load_latest_report_raises_friendly_error(tmp_path):
    with pytest.raises(ValueError, match=r"\[ADPTrust.ReportNotFound\]"):
        load_latest_report("CallCentre", tmp_path)


def test_orientation_resource_is_metadata_first_manifest():
    orientation = build_orientation_resource(_report())

    assert orientation["resource"] == "trust://products/CallCentre/orientation"
    assert orientation["data_product_id"] == "CallCentre"
    assert orientation["entrypoints"]["failures"] == "trust://products/CallCentre/failures"
    assert orientation["recommended_navigation"] == [
        "latest-report",
        "scores",
        "checks",
        "failures",
        "repair-candidates",
    ]
    assert orientation["status"]["safe_for_broad_agent_use"] is False
    assert orientation["status"]["critical_failure_count"] == 2
    assert orientation["status"]["repair_candidate_count"] == 3
    assert orientation["status"]["recommended_next_resource"] == (
        "trust://products/CallCentre/failures"
    )


def test_failures_resource_contains_consequence_and_evidence_summary():
    failures = build_failures_resource(_report())

    assert failures["failure_count"] == 3
    assert failures["failures"][0]["consequence"] == (
        "Agents or applications may query product tables directly, potentially blocking "
        "other queries that need the same table."
    )
    assert failures["failures"][0]["evidence_summary"] == (
        "MISSING_STANDARD_LOCKING_VIEW: Create the missing 1:1 locking view."
    )
    assert failures["failures"][2]["evidence_summary"] == "Backend error"


def test_repair_candidates_resource_splits_safe_auto_and_approval_required():
    repairs = build_repair_candidates_resource(_report())

    assert repairs["safe_auto_count"] == 1
    assert repairs["approval_required_count"] == 2
    assert repairs["repair_candidates"][0]["issue_code"] == "MISSING_STANDARD_LOCKING_VIEW"
    assert repairs["repair_candidates"][1]["mode"] == "safe-auto"


def test_check_explanation_returns_single_check():
    explanation = build_check_explanation(_report(), "CALLCENTRE-TEXT-001")

    assert explanation["test_id"] == "CALLCENTRE-TEXT-001"
    assert explanation["status"] == "FAILED"
    assert explanation["sample_rows"][0]["replacement"] == "relationship_paths"


def test_check_explanation_raises_friendly_error_for_missing_check():
    with pytest.raises(ValueError, match=r"\[ADPTrust.CheckNotFound\]"):
        build_check_explanation(_report(), "NOPE")


def _report(completed_at="2026-06-01T10:01:30+10:00"):
    return {
        "prefix": "CallCentre",
        "started_at": "2026-06-01T10:00:00+10:00",
        "completed_at": completed_at,
        "summary": {
            "total": 3,
            "passed": 1,
            "failed": 1,
            "errors": 1,
            "duration_seconds": 90.0,
            "duration": "1m 30s",
        },
        "scores": {
            "data_product_trust": {
                "score": 72,
                "status": "needs attention",
            }
        },
        "dimension_scores": {},
        "results": [
            {
                "status": "PASSED",
                "row_count": 0,
                "sample_rows": [],
                "test_case": {
                    "test_id": "CALLCENTRE-SEM-001",
                    "name": "Entity metadata references deployed objects",
                    "category": "SEMANTIC",
                    "severity": "CRITICAL",
                    "expected": "ZERO_ROWS",
                    "expected_result": "No missing objects.",
                    "repair_strategy": "Deploy or correct missing object metadata.",
                },
            },
            {
                "status": "FAILED",
                "row_count": 1,
                "sample_rows": [
                    {
                        "issue_code": "MISSING_STANDARD_LOCKING_VIEW",
                        "repair_hint": "Create the missing 1:1 locking view.",
                    }
                ],
                "test_case": {
                    "test_id": "CALLCENTRE-VIEW-LOCKING-001",
                    "name": "Every table has a standard locking view",
                    "category": "STRUCTURAL",
                    "severity": "CRITICAL",
                    "expected": "ZERO_ROWS",
                    "expected_result": "Every table has a corresponding view.",
                    "repair_strategy": "Create a same-named STD_V locking view.",
                },
            },
            {
                "status": "FAILED",
                "row_count": 1,
                "sample_rows": [
                    {
                        "safe_auto_apply": True,
                        "database_name": "CallCentre_SEM_STD_T",
                        "table_name": "Query_Cookbook",
                        "column_name": "recipe_description",
                        "token": "v_relationship_patsh",
                        "replacement": "relationship_paths",
                        "key_values": {"recipe_id": "R001"},
                    }
                ],
                "test_case": {
                    "test_id": "CALLCENTRE-TEXT-001",
                    "name": "Free-text references are current",
                    "category": "FREE_TEXT",
                    "severity": "WARNING",
                    "expected": "ZERO_ROWS",
                    "expected_result": "No stale aliases.",
                    "repair_strategy": "Replace stale aliases.",
                },
            },
            {
                "status": "ERROR",
                "row_count": 0,
                "sample_rows": [],
                "error_message": "Backend error",
                "test_case": {
                    "test_id": "CALLCENTRE-DISCOVERY-001",
                    "name": "Data product registry exists",
                    "category": "SEMANTIC",
                    "severity": "CRITICAL",
                    "expected": "ZERO_ROWS",
                    "expected_result": "Registry table exists.",
                    "repair_strategy": "Create the registry in the semantic module.",
                },
            },
        ],
    }
