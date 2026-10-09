import re

import pytest

from ai_native_data_product_trust_engine.cli import main
from ai_native_data_product_trust_engine.validation_ddl import (
    default_validation_view_database,
    validation_ddl,
)


def test_validation_ddl_is_rendered_for_the_requested_prefix():
    ddl = validation_ddl("SamplePrefix")

    assert "CREATE MULTISET TABLE SamplePrefix_OBS_STD_T.validation_run" in ddl
    assert "CREATE MULTISET TABLE SamplePrefix_OBS_STD_T.validation_area" in ddl
    assert "REPLACE VIEW SamplePrefix_OBS_STD_V.validation_latest" in ddl
    assert "REPLACE VIEW SamplePrefix_OBS_STD_V.validation_trust_map" in ddl
    assert "FROM SamplePrefix_OBS_STD_T.validation_run" in ddl
    assert "ON SamplePrefix_OBS_STD_T.validation_area;" in ddl
    assert "ExampleProduct" not in ddl
    assert "{" not in ddl and "}" not in ddl


def test_validation_ddl_keeps_the_wire_schema_contract():
    ddl = validation_ddl("SamplePrefix")

    assert "payload_schema_version VARCHAR(8) CHARACTER SET LATIN NOT NULL DEFAULT '2.1'" in ddl
    assert "CHECK (scope_kind IN ('MODULE', 'ENTITY', 'PATTERN', 'CAPABILITY', 'PRODUCT'))" in ddl
    assert "CHECK (area_status IN ('pass', 'fail', 'partial', 'not-validated', 'no-evidence'))" in ddl
    assert "CHECK (confidence IN ('strong', 'partial', 'weak', 'unknown'))" in ddl
    assert "ORDER BY completed_dts DESC, run_id DESC" in ddl
    assert "WHERE v.payload_schema_version IN ('1.0', '2.0')" in ddl
    assert "'PUBLISHED' AS map_source" in ddl
    assert "'DERIVED' AS map_source" in ddl


def test_validation_ddl_accepts_explicit_table_and_view_databases():
    ddl = validation_ddl("SamplePrefix", table_database="SamplePrefix_Observability", view_database="SamplePrefix_Obs_V")

    assert "CREATE MULTISET TABLE SamplePrefix_Observability.validation_run" in ddl
    assert "REPLACE VIEW SamplePrefix_Obs_V.validation_trust_map" in ddl
    assert "FROM SamplePrefix_Observability.validation_area AS la" in ddl
    assert "SamplePrefix_OBS_STD_T" not in ddl


def test_validation_ddl_comments_fit_teradata_comment_limit():
    # Teradata rejects an over-length COMMENT string (error 5550); 254 is the standing limit.
    comments = re.findall(r"COMMENT ON \w+ [\w.]+ IS\s*'((?:[^']|'')*)';", validation_ddl("SamplePrefix"))

    assert comments
    assert [c for c in comments if len(c.replace("''", "'")) > 254] == []


def test_validation_ddl_adds_an_access_layer_trust_map_over_the_std_view():
    ddl = validation_ddl("SamplePrefix")

    assert "REPLACE VIEW SamplePrefix_OBS_ACL_V.validation_trust_map" in ddl
    assert "FROM SamplePrefix_OBS_STD_V.validation_trust_map;" in ddl
    assert "COMMENT ON VIEW SamplePrefix_OBS_ACL_V.validation_trust_map" in ddl
    assert "__ACL_DB__" not in ddl


def test_validation_ddl_accepts_explicit_acl_view_database():
    ddl = validation_ddl("SamplePrefix", acl_view_database="SamplePrefix_Acl")

    assert "REPLACE VIEW SamplePrefix_Acl.validation_trust_map" in ddl
    assert "SamplePrefix_OBS_ACL_V" not in ddl


def test_validation_ddl_defaults_view_database_to_obs_std_v():
    assert default_validation_view_database("SamplePrefix") == "SamplePrefix_OBS_STD_V"


@pytest.mark.parametrize("bad", ["SamplePrefix.OBS", "SamplePrefix OBS", "1SamplePrefix", "Mort;DROP"])
def test_validation_ddl_rejects_invalid_database_names(bad):
    with pytest.raises(ValueError, match=r"\[ADPTrust\.InvalidTrustTable\]"):
        validation_ddl("SamplePrefix", table_database=bad)


def test_validation_ddl_cli_writes_file(tmp_path, capsys):
    output = tmp_path / "sampleprefix_validation_ddl.sql"

    exit_code = main(["validation-ddl", "--prefix", "SamplePrefix", "--output", str(output)])

    assert exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert "CREATE MULTISET TABLE SamplePrefix_OBS_STD_T.validation_run" in text
    assert str(output) in capsys.readouterr().out


def test_validation_ddl_cli_prints_to_stdout_without_output(capsys):
    exit_code = main(["validation-ddl", "--prefix", "SamplePrefix"])

    assert exit_code == 0
    assert "REPLACE VIEW SamplePrefix_OBS_STD_V.validation_trust_map" in capsys.readouterr().out


def test_validation_ddl_cli_honours_rules_config_publish_database(tmp_path, capsys):
    rules = tmp_path / "rules.json"
    rules.write_text('{"publish_validation_database": "SamplePrefix_Observability"}', encoding="utf-8")

    exit_code = main(["validation-ddl", "--prefix", "SamplePrefix", "--rules-config", str(rules)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "CREATE MULTISET TABLE SamplePrefix_Observability.validation_run" in out
    assert "REPLACE VIEW SamplePrefix_OBS_STD_V.validation_trust_map" in out
