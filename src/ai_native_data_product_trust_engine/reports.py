"""Report serialisation for validation evidence."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ai_native_data_product_trust_engine.error_formatting import concise_backend_error
from ai_native_data_product_trust_engine.models import TestResult, ValidationRun
from ai_native_data_product_trust_engine.scoring import (
    dimension_scores,
    scorecards,
)


def validation_run_to_dict(run: ValidationRun) -> dict[str, object]:
    return {
        "prefix": run.prefix,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "summary": {
            "total": len(run.results),
            "passed": run.passed_count,
            "failed": run.failed_count,
            "errors": run.error_count,
        },
        "scores": scorecards(run.results),
        "dimension_scores": dimension_scores(run.results),
        "results": [_result_to_dict(result) for result in run.results],
    }


def write_json_report(run: ValidationRun, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(validation_run_to_dict(run), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _result_to_dict(result: TestResult) -> dict[str, object]:
    payload = asdict(result)
    payload["status"] = result.status.value
    payload["test_case"]["category"] = result.test_case.category.value
    payload["test_case"]["severity"] = result.test_case.severity.value
    payload["test_case"]["expected"] = result.test_case.expected.value
    if result.error_message:
        payload["error_message"] = concise_backend_error(result.error_message)
    return payload
