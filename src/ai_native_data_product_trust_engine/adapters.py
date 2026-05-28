"""Database adapters for validation execution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse


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
