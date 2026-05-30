"""Score validation results into separate readiness families."""

from __future__ import annotations

from ai_native_data_product_trust_engine.models import (
    TestCategory,
    TestResult,
    TestSeverity,
    TestStatus,
)

_SEVERITY_WEIGHTS = {
    TestSeverity.CRITICAL: 40,
    TestSeverity.ERROR: 25,
    TestSeverity.WARNING: 10,
    TestSeverity.INFO: 5,
}

_DATA_PRODUCT_TRUST_CATEGORIES = {
    TestCategory.STRUCTURAL,
    TestCategory.SEMANTIC,
    TestCategory.QUERY,
    TestCategory.CAPABILITY,
    TestCategory.DATA_QUALITY,
    TestCategory.FREE_TEXT,
}
_PERFORMANCE_READINESS_CATEGORIES = {TestCategory.PERFORMANCE}
_OPERATIONAL_READINESS_CATEGORIES = {TestCategory.OPERATIONAL}


def scorecards(results: list[TestResult]) -> dict[str, dict[str, object]]:
    return {
        "data_product_trust": _score_summary(
            results,
            _DATA_PRODUCT_TRUST_CATEGORIES,
            "Data product trust",
        ),
        "performance_readiness": _score_summary(
            results,
            _PERFORMANCE_READINESS_CATEGORIES,
            "Performance readiness",
        ),
        "operational_readiness": _score_summary(
            results,
            _OPERATIONAL_READINESS_CATEGORIES,
            "Operational readiness",
        ),
    }


def dimension_scores(results: list[TestResult]) -> dict[str, int]:
    categories = sorted({result.test_case.category.value for result in results})
    return {
        category: score(
            [result for result in results if result.test_case.category.value == category]
        )
        for category in categories
    }


def score(results: list[TestResult]) -> int:
    if not results:
        return 100
    total = sum(_weight(result) for result in results)
    earned = sum(_weight(result) for result in results if result.status == TestStatus.PASSED)
    return round((earned / total) * 100) if total else 100


def score_message(score_value: int, label: str = "Data product trust") -> str:
    if score_value >= 90:
        return f"{label} is healthy. Review remaining warnings and keep evidence current."
    if score_value >= 70:
        return (
            f"{label} is usable with targeted fixes. Resolve critical failures before broad agent "
            "use."
        )
    return (
        f"{label} is low. Prioritise critical failures and safe repairs before relying on "
        "generated SQL."
    )


def _score_summary(
    results: list[TestResult],
    categories: set[TestCategory],
    label: str,
) -> dict[str, object]:
    scoped_results = [
        result for result in results if result.test_case.category in categories
    ]
    if not scoped_results:
        return {
            "label": label,
            "score": None,
            "assessed": False,
            "test_count": 0,
            "message": "No checks in this score family have run yet.",
        }
    score_value = score(scoped_results)
    return {
        "label": label,
        "score": score_value,
        "assessed": True,
        "test_count": len(scoped_results),
        "message": score_message(score_value, label),
    }


def _weight(result: TestResult) -> int:
    return _SEVERITY_WEIGHTS.get(result.test_case.severity, 5)
