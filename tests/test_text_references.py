from ai_native_data_product_trust_engine.text_references import (
    ReferenceClassification,
    TextMetadataSource,
    apply_safe_text_repairs,
    find_text_reference_issues,
    run_text_reference_validation,
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


def test_run_text_reference_validation_reports_source_context():
    source = TextMetadataSource(
        test_id="CALLCENTRE-TEXT-001",
        name="Free-text scan",
        database_name="CallCentre_MEM_STD_V",
        table_name="Query_Cookbook",
        key_columns=("recipe_id",),
        text_columns=("recipe_description",),
    )
    adapter = StubAdapter(
        rows=[
            {
                "recipe_id": "QCB-001",
                "recipe_description": "Uses v_relationship_paths for join discovery.",
            }
        ]
    )

    result = run_text_reference_validation(adapter, source)

    assert result.status.value == "FAILED"
    assert result.row_count == 1
    assert result.sample_rows[0] == {
        "database_name": "CallCentre_MEM_STD_V",
        "table_name": "Query_Cookbook",
        "column_name": "recipe_description",
        "row_key": "recipe_id=QCB-001",
        "key_values": {"recipe_id": "QCB-001"},
        "token": "v_relationship_paths",
        "replacement": "relationship_paths",
        "classification": "STALE_ALIAS",
        "safe_auto_apply": True,
    }


class StubAdapter:
    def __init__(self, rows):
        self.rows = rows

    def fetch_all(self, sql):
        return self.rows
