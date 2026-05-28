"""Core model types for generated trust tests and repair decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TestCategory(str, Enum):
    STRUCTURAL = "STRUCTURAL"
    SEMANTIC = "SEMANTIC"
    QUERY = "QUERY"
    CAPABILITY = "CAPABILITY"
    DATA_QUALITY = "DATA_QUALITY"


class TestSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class RepairMode(str, Enum):
    DETECT = "detect"
    PROPOSAL = "proposal"
    SAFE_AUTO = "safe-auto"


@dataclass(frozen=True)
class TestCase:
    test_id: str
    name: str
    category: TestCategory
    severity: TestSeverity
    sql: str
    expected_result: str
    repair_strategy: str | None = None
