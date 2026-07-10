"""User-facing formatting for backend validation errors."""

from __future__ import annotations


def concise_backend_error(error_message: str) -> str:
    message = error_message.strip()
    for marker in ("\n at ", ". at ", " at gosqldriver/", " at database/sql."):
        if marker in message:
            message = message.split(marker, maxsplit=1)[0]
            break
    if message.startswith("RuntimeError:"):
        message = message.removeprefix("RuntimeError:").strip()
    return message.rstrip(".") + "."


# Backend error fragments that mean the database itself cannot be reached or is
# refusing to do work — no session, a refused/broken connection, exhausted
# virtual circuits, or rejected credentials. These are infrastructure failures,
# not a fault in one check's SQL, so a validation run should stop rather than
# record the identical failure against every remaining check.
_DATABASE_UNAVAILABLE_MARKERS = (
    "all virtual circuits are currently in use",  # Teradata 8024
    "error 8024",
    "hostname lookup failed",
    "failed to connect",
    "unable to connect",
    "could not connect",
    "connection refused",
    "connection reset",
    "connection timed out",
    "socket communication error",
    "no connection could be made",
    "the database is not available",
    "logon timeout",
    "error 8017",  # invalid UserId/Password/Account — no session can be established
    "error 8018",
)


def is_database_unavailable_error(error_message: str) -> bool:
    """True when a backend error means the database can't be reached or used at all.

    Distinguishes an infrastructure/availability failure (no session, refused
    connection, exhausted virtual circuits, bad credentials) from a fault in a
    single check's SQL. When true, the run should stop instead of soldiering on
    and recording the same error against every remaining check.
    """
    lowered = error_message.lower()
    return any(marker in lowered for marker in _DATABASE_UNAVAILABLE_MARKERS)
