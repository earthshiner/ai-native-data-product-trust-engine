from ai_native_data_product_trust_engine.text_references import (
    ReferenceClassification,
    apply_safe_text_repairs,
    find_text_reference_issues,
)


def test_find_text_reference_issues_detects_stale_alias_and_typo():
    text = "Use v_relationship_paths, not v_relationship_patsh, for relationship discovery."

    issues = find_text_reference_issues(text)

    assert [issue.classification for issue in issues] == [
        ReferenceClassification.STALE_ALIAS,
        ReferenceClassification.TYPO_SUSPECT,
    ]
    assert [issue.replacement for issue in issues] == [
        "relationship_paths",
        "relationship_paths",
    ]
    assert all(issue.safe_auto_apply for issue in issues)


def test_apply_safe_text_repairs_replaces_known_tokens_only():
    text = "v_relationship_paths should change; my_v_relationship_paths_backup should not."

    repaired = apply_safe_text_repairs(text)

    assert repaired == (
        "relationship_paths should change; my_v_relationship_paths_backup should not."
    )
