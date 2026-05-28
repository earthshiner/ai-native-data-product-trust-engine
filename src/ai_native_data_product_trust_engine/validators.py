"""Validation orchestration contracts.

The first implementation will keep database access behind an adapter so tests can be
unit-tested without a live Teradata connection.
"""

from __future__ import annotations

from typing import Protocol

from ai_native_data_product_trust_engine.models import TestCase


class DatabaseAdapter(Protocol):
    def fetch_all(self, sql: str) -> list[dict[str, object]]:
        """Run SQL and return rows as dictionaries."""


def run_test_case(adapter: DatabaseAdapter, test_case: TestCase) -> bool:
    rows = adapter.fetch_all(test_case.sql)
    return len(rows) == 0
