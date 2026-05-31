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
