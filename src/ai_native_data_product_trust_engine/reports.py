"""Report serialisation for validation evidence."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from ai_native_data_product_trust_engine.error_formatting import concise_backend_error
from ai_native_data_product_trust_engine.models import TestResult, ValidationRun
from ai_native_data_product_trust_engine.scoring import (
    dimension_scores,
    scorecards,
)


def validation_run_to_dict(run: ValidationRun) -> dict[str, object]:
    duration_seconds = validation_run_duration_seconds(run)
    return {
        "prefix": run.prefix,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "summary": {
            "total": len(run.results),
            "passed": run.passed_count,
            "failed": run.failed_count,
            "errors": run.error_count,
            "duration_seconds": duration_seconds,
            "duration": format_duration(duration_seconds),
        },
        "scores": scorecards(run.results),
        "dimension_scores": dimension_scores(run.results),
        "results": [_result_to_dict(result) for result in run.results],
    }


def write_json_report(run: ValidationRun, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(_json_safe(validation_run_to_dict(run)), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def validation_run_duration_seconds(run: ValidationRun) -> float:
    try:
        started_at = _parse_datetime(run.started_at)
        completed_at = _parse_datetime(run.completed_at)
    except ValueError:
        return 0.0
    return max((completed_at - started_at).total_seconds(), 0.0)


def format_duration(duration_seconds: float) -> str:
    if duration_seconds < 1:
        return f"{duration_seconds:.2f}s"
    rounded_seconds = int(round(duration_seconds))
    hours, remainder = divmod(rounded_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _result_to_dict(result: TestResult) -> dict[str, object]:
    payload = asdict(result)
    payload["status"] = result.status.value
    payload["test_case"]["category"] = result.test_case.category.value
    payload["test_case"]["severity"] = result.test_case.severity.value
    payload["test_case"]["expected"] = result.test_case.expected.value
    if result.error_message:
        payload["error_message"] = concise_backend_error(result.error_message)
    return _json_safe(payload)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    return str(value)
