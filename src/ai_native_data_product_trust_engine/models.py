"""Core model types for generated trust tests and repair decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_native_data_product_trust_engine.layout import Layout


class TestCategory(str, Enum):
    __test__ = False

    STRUCTURAL = "STRUCTURAL"
    SEMANTIC = "SEMANTIC"
    QUERY = "QUERY"
    CAPABILITY = "CAPABILITY"
    PERFORMANCE = "PERFORMANCE"
    OPERATIONAL = "OPERATIONAL"
    DATA_QUALITY = "DATA_QUALITY"
    FREE_TEXT = "FREE_TEXT"


class TestSeverity(str, Enum):
    __test__ = False

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class RepairMode(str, Enum):
    DETECT = "detect"
    PROPOSAL = "proposal"
    SAFE_AUTO = "safe-auto"


class ExpectedResult(str, Enum):
    ZERO_ROWS = "ZERO_ROWS"
    NON_EMPTY = "NON_EMPTY"


class TestStatus(str, Enum):
    __test__ = False

    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class TestCase:
    __test__ = False

    test_id: str
    name: str
    category: TestCategory
    severity: TestSeverity
    sql: str
    expected_result: str
    expected: ExpectedResult = ExpectedResult.ZERO_ROWS
    repair_strategy: str | None = None
    inspection_scope: str | None = None
    precondition_sql: str | None = None


@dataclass(frozen=True)
class TestResult:
    __test__ = False

    test_case: TestCase
    status: TestStatus
    row_count: int
    sample_rows: list[dict[str, object]] = field(default_factory=list)
    error_message: str | None = None


@dataclass(frozen=True)
class ExcludedCheck:
    __test__ = False

    check_id: str
    name: str
    category: str
    reason: str
    # False for a check that does not apply to this product's declared layout
    # (Platform Layout Standard section 7): it is reported with its reason but is
    # not part of the area's expected checks, so exclusion does not lower coverage.
    counts_as_expected: bool = True


@dataclass(frozen=True)
class ValidationRun:
    prefix: str
    started_at: str
    completed_at: str
    results: list[TestResult]
    excluded_checks: list[ExcludedCheck] = field(default_factory=list)
    # The resolved layout (a ``layout.Layout``) the run used, when known.
    layout: Layout | None = None

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.status == TestStatus.PASSED)

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if result.status == TestStatus.FAILED)

    @property
    def error_count(self) -> int:
        return sum(1 for result in self.results if result.status == TestStatus.ERROR)
