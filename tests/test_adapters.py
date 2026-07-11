"""Adapter connection lifecycle — one pooled engine per run, disposed promptly.

Creating a fresh SQLAlchemy engine per query leaked an idle Teradata session
each time (a fast path to Error 8024). These tests pin that the engine is built
once and reused, that ``close()`` / context-manager exit dispose it, and that
the CLI releases the session even when the run aborts.
"""

import pytest

from ai_native_data_product_trust_engine import cli
from ai_native_data_product_trust_engine.adapters import LoggingAdapter, SqlAlchemyAdapter


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeConnection:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, statement):
        self._engine.executed.append(str(statement))
        return _FakeResult([_FakeRow({"n": 1})])


class _FakeEngine:
    def __init__(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        self.connect_calls = 0
        self.dispose_calls = 0
        self.executed = []

    def connect(self):
        self.connect_calls += 1
        return _FakeConnection(self)

    def begin(self):
        self.connect_calls += 1
        return _FakeConnection(self)

    def dispose(self):
        self.dispose_calls += 1


def _patch_engine_factory(monkeypatch):
    # sqlalchemy is an optional dependency (teradata extra). The SqlAlchemyAdapter
    # tests need it; skip them where it is absent (e.g. the dev-only CI job).
    sqlalchemy = pytest.importorskip("sqlalchemy")
    created: list[_FakeEngine] = []

    def _fake_create_engine(url, **kwargs):
        engine = _FakeEngine(url, **kwargs)
        created.append(engine)
        return engine

    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)
    return created


def test_sqlalchemy_adapter_reuses_one_bounded_engine(monkeypatch):
    created = _patch_engine_factory(monkeypatch)
    adapter = SqlAlchemyAdapter("teradatasql://u:p@host/db")

    adapter.fetch_all("SELECT 1;")
    adapter.fetch_all("SELECT 2;")
    adapter.execute("UPDATE t SET c = 1;")

    assert len(created) == 1  # one engine reused, not one per query
    assert created[0].connect_calls == 3  # every call used the pooled engine
    assert created[0].kwargs.get("pool_size") == 1
    assert created[0].kwargs.get("max_overflow") == 0
    assert created[0].kwargs.get("pool_pre_ping") is True


def test_sqlalchemy_adapter_close_disposes_and_can_rebuild(monkeypatch):
    created = _patch_engine_factory(monkeypatch)
    adapter = SqlAlchemyAdapter("teradatasql://u:p@host/db")

    adapter.fetch_all("SELECT 1;")
    assert created[0].dispose_calls == 0

    adapter.close()
    assert created[0].dispose_calls == 1

    # A later call lazily builds a fresh engine (idempotent close in between).
    adapter.close()
    adapter.fetch_all("SELECT 2;")
    assert len(created) == 2


def test_sqlalchemy_adapter_context_manager_disposes(monkeypatch):
    created = _patch_engine_factory(monkeypatch)

    with SqlAlchemyAdapter("teradatasql://u:p@host/db") as adapter:
        adapter.fetch_all("SELECT 1;")

    assert created[0].dispose_calls == 1


class _ClosableStub:
    def __init__(self):
        self.closed = False

    def fetch_all(self, sql):
        return []

    def execute(self, sql):
        return None

    def close(self):
        self.closed = True


def test_logging_adapter_close_delegates_to_wrapped_adapter():
    stub = _ClosableStub()
    LoggingAdapter(stub).close()
    assert stub.closed is True


def test_logging_adapter_close_is_safe_without_wrapped_close():
    class _NoClose:
        def fetch_all(self, sql):
            return []

        def execute(self, sql):
            return None

    LoggingAdapter(_NoClose()).close()  # must not raise


def test_cli_releases_session_even_when_run_aborts(monkeypatch, tmp_path):
    """The validate ``finally`` disposes the adapter despite a fatal DB error."""

    class _AbortingClosable:
        def __init__(self):
            self.closed = False

        def fetch_all(self, sql):
            raise RuntimeError(
                "[Session 0] [Teradata Database] [Error 8024] "
                "All virtual circuits are currently in use."
            )

        def execute(self, sql):
            raise AssertionError("execute should not run")

        def close(self):
            self.closed = True

    adapter = _AbortingClosable()
    monkeypatch.setattr(cli, "adapter_from_environment", lambda url: adapter)

    code = cli.main(
        ["validate", "--prefix", "CallCentre", "--database-url", "teradatasql://x",
         "--output", str(tmp_path / "report.json")]
    )

    assert code == 3
    assert adapter.closed is True  # session released on the way out
