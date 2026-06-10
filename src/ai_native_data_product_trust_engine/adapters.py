"""Database adapters for validation execution."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

LOGGER = logging.getLogger("ai_native_data_product_trust_engine.sql")


class DatabaseAdapter(Protocol):
    def fetch_all(self, sql: str) -> list[dict[str, object]]:
        """Run SQL and return rows as dictionaries."""

    def fetch_all_with_session_setup(
        self,
        sql: str,
        setup_sql: str | None = None,
        teardown_sql: str | None = None,
    ) -> list[dict[str, object]]:
        """Run SQL with optional setup/teardown statements on the same session."""

    def execute(self, sql: str) -> None:
        """Execute a non-query SQL statement."""


@dataclass(frozen=True)
class LoggingAdapter:
    adapter: DatabaseAdapter

    def fetch_all(self, sql: str) -> list[dict[str, object]]:
        LOGGER.info("Executing SQL query:\n%s", sql)
        try:
            rows = self.adapter.fetch_all(sql)
        except Exception as exc:
            _log_query_failure(sql, exc)
            raise
        LOGGER.info("SQL query returned %s rows.", len(rows))
        return rows

    def fetch_all_with_session_setup(
        self,
        sql: str,
        setup_sql: str | None = None,
        teardown_sql: str | None = None,
    ) -> list[dict[str, object]]:
        if setup_sql:
            LOGGER.info("Executing session setup SQL before query:\n%s", setup_sql)
        LOGGER.info("Executing SQL query:\n%s", sql)
        if teardown_sql:
            LOGGER.info("Executing session teardown SQL after query:\n%s", teardown_sql)
        try:
            rows = self.adapter.fetch_all_with_session_setup(sql, setup_sql, teardown_sql)
        except Exception as exc:
            _log_query_failure(sql, exc)
            raise
        LOGGER.info("SQL query returned %s rows.", len(rows))
        return rows

    def execute(self, sql: str) -> None:
        LOGGER.info("Executing SQL statement:\n%s", sql)
        try:
            self.adapter.execute(sql)
        except Exception:
            LOGGER.exception("SQL statement failed:\n%s", sql)
            raise
        LOGGER.info("SQL statement completed.")


@dataclass(frozen=True)
class SqlAlchemyAdapter:
    database_url: str

    def fetch_all(self, sql: str) -> list[dict[str, object]]:
        try:
            from sqlalchemy import create_engine, text
        except ImportError as exc:
            msg = (
                "[ADPTrust.MissingDependency] SQLAlchemy is required for live validation. "
                "Suggested action: install the teradata optional dependencies."
            )
            raise RuntimeError(msg) from exc

        engine = create_engine(_normalise_database_url(self.database_url))
        with engine.connect() as connection:
            rows = connection.execute(text(sql))
            return [dict(row._mapping) for row in rows]

    def fetch_all_with_session_setup(
        self,
        sql: str,
        setup_sql: str | None = None,
        teardown_sql: str | None = None,
    ) -> list[dict[str, object]]:
        try:
            from sqlalchemy import create_engine, text
        except ImportError as exc:
            msg = (
                "[ADPTrust.MissingDependency] SQLAlchemy is required for live validation. "
                "Suggested action: install the teradata optional dependencies."
            )
            raise RuntimeError(msg) from exc

        engine = create_engine(_normalise_database_url(self.database_url))
        with engine.connect() as connection:
            setup_completed = False
            try:
                if setup_sql:
                    connection.execute(text(setup_sql))
                    setup_completed = True
                rows = connection.execute(text(sql))
                return [dict(row._mapping) for row in rows]
            finally:
                if teardown_sql and setup_completed:
                    try:
                        connection.execute(text(teardown_sql))
                    except Exception:  # noqa: BLE001 - preserve the original query failure.
                        LOGGER.exception("Session teardown SQL failed:\n%s", teardown_sql)

    def execute(self, sql: str) -> None:
        try:
            from sqlalchemy import create_engine, text
        except ImportError as exc:
            msg = (
                "[ADPTrust.MissingDependency] SQLAlchemy is required for live repair. "
                "Suggested action: install the teradata optional dependencies."
            )
            raise RuntimeError(msg) from exc

        engine = create_engine(_normalise_database_url(self.database_url))
        with engine.begin() as connection:
            connection.execute(text(sql))


@dataclass(frozen=True)
class TeradataSqlAdapter:
    database_url: str

    def fetch_all(self, sql: str) -> list[dict[str, object]]:
        try:
            import teradatasql
        except ImportError as exc:
            msg = (
                "[ADPTrust.MissingDependency] teradatasql is required for live validation. "
                "Suggested action: install the teradata optional dependencies."
            )
            raise RuntimeError(msg) from exc

        connection_args = _teradatasql_args_from_url(self.database_url)
        with teradatasql.connect(**connection_args) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                columns = [column[0] for column in cursor.description or []]
                return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def fetch_all_with_session_setup(
        self,
        sql: str,
        setup_sql: str | None = None,
        teardown_sql: str | None = None,
    ) -> list[dict[str, object]]:
        try:
            import teradatasql
        except ImportError as exc:
            msg = (
                "[ADPTrust.MissingDependency] teradatasql is required for live validation. "
                "Suggested action: install the teradata optional dependencies."
            )
            raise RuntimeError(msg) from exc

        connection_args = _teradatasql_args_from_url(self.database_url)
        with teradatasql.connect(**connection_args) as connection:
            with connection.cursor() as cursor:
                setup_completed = False
                try:
                    if setup_sql:
                        cursor.execute(setup_sql)
                        setup_completed = True
                    cursor.execute(sql)
                    columns = [column[0] for column in cursor.description or []]
                    return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
                finally:
                    if teardown_sql and setup_completed:
                        try:
                            cursor.execute(teardown_sql)
                        except Exception:  # noqa: BLE001 - preserve the original query failure.
                            LOGGER.exception("Session teardown SQL failed:\n%s", teardown_sql)

    def execute(self, sql: str) -> None:
        try:
            import teradatasql
        except ImportError as exc:
            msg = (
                "[ADPTrust.MissingDependency] teradatasql is required for live repair. "
                "Suggested action: install the teradata optional dependencies."
            )
            raise RuntimeError(msg) from exc

        connection_args = _teradatasql_args_from_url(self.database_url)
        with teradatasql.connect(**connection_args) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)


def adapter_from_environment(database_url: str | None = None):
    resolved_url = database_url or os.environ.get("DATABASE_URI")
    if not resolved_url:
        msg = (
            "[ADPTrust.DatabaseUriMissing] No database URL was provided. "
            "Suggested action: pass --database-url or set DATABASE_URI."
        )
        raise RuntimeError(msg)

    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        return TeradataSqlAdapter(resolved_url)

    return SqlAlchemyAdapter(resolved_url)


def configure_logging(log_level: str | None = None, log_file: Path | None = None) -> None:
    resolved_level = _log_level(log_level or ("INFO" if log_file else "WARNING"))
    handlers: list[logging.Handler] = []
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    else:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=resolved_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def _log_query_failure(sql: str, exc: Exception) -> None:
    concise_error = str(exc).split("\n at ", maxsplit=1)[0].strip()
    display_sql = sql.replace("\r\n", "\n").replace("\r", "\n")
    LOGGER.error("SQL query failed: %s\n%s", concise_error, display_sql)
    LOGGER.debug("SQL query failure traceback.", exc_info=exc)


def _log_level(value: str) -> int:
    level_name = value.upper()
    if level_name not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        msg = (
            f"[ADPTrust.InvalidLogLevel] Unsupported log level {value}. "
            "Suggested action: use DEBUG, INFO, WARNING, ERROR or CRITICAL."
        )
        raise ValueError(msg)
    return int(getattr(logging, level_name))


def _normalise_database_url(database_url: str) -> str:
    if database_url.startswith("teradata://"):
        return database_url.replace("teradata://", "teradatasql://", 1)
    return database_url


def _teradatasql_args_from_url(database_url: str) -> dict[str, str]:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"teradata", "teradatasql"}:
        msg = (
            f"[ADPTrust.UnsupportedDatabaseUrl] Unsupported database URL scheme {parsed.scheme}. "
            "Suggested action: use teradata:// or teradatasql://."
        )
        raise RuntimeError(msg)

    query = parse_qs(parsed.query)
    args = {
        "host": parsed.hostname or "",
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }
    if parsed.port:
        args["dbs_port"] = str(parsed.port)
    if parsed.path and parsed.path != "/":
        args["database"] = parsed.path.strip("/")
    for key, values in query.items():
        if values:
            args[key] = values[-1]
    return args
