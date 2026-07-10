"""Friendly CLI errors surface the DATABASE_URI format and the real cause."""

from ai_native_data_product_trust_engine.adapters import (
    DATABASE_URI_FORMAT,
    adapter_from_environment,
    database_uri_hint,
)
from ai_native_data_product_trust_engine.cli import _friendly_cli_error


def test_database_uri_hint_shows_the_scheme_and_shape():
    hint = database_uri_hint()
    assert "teradatasql://" in hint
    assert DATABASE_URI_FORMAT in hint


def test_generic_failure_includes_uri_format():
    message = _friendly_cli_error(RuntimeError("connection reset by peer"))
    assert "[ADPTrust.ValidationFailed]" in message
    assert "connection reset by peer" in message
    assert DATABASE_URI_FORMAT in message


def test_missing_dialect_error_is_identified_not_blamed_on_the_uri():
    exc = RuntimeError("Can't load plugin: sqlalchemy.dialects:teradatasql")
    message = _friendly_cli_error(exc)
    assert "[ADPTrust.MissingDialect]" in message
    assert ".[teradata]" in message  # tells the user how to fix it
    assert "adp.ps1" in message
    assert DATABASE_URI_FORMAT in message  # still shows the format for good measure


def test_adptrust_messages_pass_through_unchanged():
    original = "[ADPTrust.DatabaseUriMissing] No database URL was provided. …"
    assert _friendly_cli_error(RuntimeError(original)) == original


def test_missing_database_uri_message_includes_format(monkeypatch):
    monkeypatch.delenv("DATABASE_URI", raising=False)
    try:
        adapter_from_environment(None)
    except RuntimeError as exc:
        assert "[ADPTrust.DatabaseUriMissing]" in str(exc)
        assert DATABASE_URI_FORMAT in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("adapter_from_environment(None) should raise")
