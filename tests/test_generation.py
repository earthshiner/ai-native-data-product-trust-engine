from ai_native_data_product_trust_engine.repairs import classify_stale_relationship_path_name
from ai_native_data_product_trust_engine.test_generation import generate_metadata_tests


def test_generate_metadata_tests_includes_core_contracts():
    tests = generate_metadata_tests("CallCentre")

    assert [test.test_id for test in tests] == [
        "CALLCENTRE-SEM-001",
        "CALLCENTRE-SEM-002",
        "CALLCENTRE-SEM-003",
        "CALLCENTRE-QUERY-001",
    ]
    assert all("CallCentre_" in test.sql for test in tests)


def test_classify_stale_relationship_path_name():
    candidate = classify_stale_relationship_path_name("v_relationship_patsh")

    assert candidate is not None
    assert candidate.issue_code == "STALE_OBJECT_NAME"
    assert candidate.requires_approval is False
