"""Standalone HTML report rendering for human trust review."""

from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path

from ai_native_data_product_trust_engine.models import (
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.repairs import RepairCandidate
from ai_native_data_product_trust_engine.reports import validation_run_to_dict

_SEVERITY_WEIGHTS = {
    TestSeverity.CRITICAL: 40,
    TestSeverity.ERROR: 25,
    TestSeverity.WARNING: 10,
    TestSeverity.INFO: 5,
}


def write_html_report(
    run: ValidationRun,
    output_path: Path,
    repair_candidates: list[RepairCandidate] | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_html_report(run, repair_candidates or []),
        encoding="utf-8",
    )


def render_html_report(
    run: ValidationRun,
    repair_candidates: list[RepairCandidate],
) -> str:
    results = sorted(run.results, key=_result_sort_key)
    score = _score(results)
    dimension_scores = _dimension_scores(results)
    dependency_index = _dependency_index(results)
    root_cause_groups = _root_cause_groups(results, dependency_index)
    safe_auto_count = sum(1 for candidate in repair_candidates if not candidate.requires_approval)
    approval_count = sum(1 for candidate in repair_candidates if candidate.requires_approval)
    safe_auto_panel = _repair_panel(
        "Safe-auto candidates",
        safe_auto_count,
        "Deterministic repairs that can run without steward approval.",
    )
    approval_panel = _repair_panel(
        "Approval required",
        approval_count,
        "Repairs that need human judgement before metadata changes.",
    )
    data_json = _json_for_html(
        {
            "validation": validation_run_to_dict(run),
            "repair_candidates": [_repair_to_dict(candidate) for candidate in repair_candidates],
            "score": score,
            "dimension_scores": dimension_scores,
            "root_cause_groups": root_cause_groups,
        }
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_h(run.prefix)} metadata trust report</title>
  <style>
    :root {{
      --td-orange: #FF5F02;
      --td-navy: #00233C;
      --td-white: #FFFFFF;
      --td-line: #D9E2EA;
      --td-muted: #5B6B7A;
      --td-bg: #F7F9FB;
      --td-green: #1D7F43;
      --td-red: #B42318;
      --td-amber: #A15C00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--td-bg);
      color: var(--td-navy);
      font-family: Inter, Arial, sans-serif;
      line-height: 1.45;
    }}
    header {{
      background: var(--td-navy);
      color: var(--td-white);
      padding: 28px 32px;
      border-bottom: 6px solid var(--td-orange);
    }}
    header .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 18px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .brand-mark {{
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: var(--td-orange);
      display: inline-block;
    }}
    h1 {{
      margin: 0;
      font-size: 34px;
      font-weight: 300;
      letter-spacing: 0;
    }}
    header p {{
      max-width: 960px;
      margin: 10px 0 0;
      color: #DDE8F0;
    }}
    main {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 28px 24px 48px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: 1.25fr repeat(4, minmax(120px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .panel {{
      background: var(--td-white);
      border: 1px solid var(--td-line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 1px 2px rgba(0, 35, 60, 0.06);
    }}
    .score {{
      display: flex;
      align-items: center;
      gap: 18px;
    }}
    .score-value {{
      width: 98px;
      height: 98px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      color: var(--td-white);
      background: conic-gradient(var(--td-orange) {score}%, #D9E2EA {score}%);
      position: relative;
      font-size: 28px;
      font-weight: 700;
    }}
    .score-value::after {{
      content: "";
      position: absolute;
      inset: 8px;
      border-radius: 50%;
      background: var(--td-navy);
      z-index: 0;
    }}
    .score-value span {{ position: relative; z-index: 1; }}
    .metric-label {{
      color: var(--td-muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }}
    .metric-value {{
      margin-top: 6px;
      font-size: 30px;
      font-weight: 700;
    }}
    .dimension-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}
    .bar {{
      margin-top: 10px;
      height: 8px;
      background: #E8EEF3;
      border-radius: 999px;
      overflow: hidden;
    }}
    .bar > span {{
      display: block;
      height: 100%;
      background: var(--td-orange);
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    select, input {{
      width: 100%;
      border: 1px solid var(--td-line);
      border-radius: 6px;
      padding: 10px 12px;
      font: inherit;
      color: var(--td-navy);
      background: var(--td-white);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--td-white);
      border: 1px solid var(--td-line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      text-align: left;
      padding: 12px 14px;
      border-bottom: 1px solid var(--td-line);
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      background: #EEF3F7;
      font-size: 12px;
      text-transform: uppercase;
      color: var(--td-muted);
    }}
    code {{
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      background: #F1F5F8;
      padding: 2px 5px;
      border-radius: 4px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      font-weight: 700;
    }}
    .PASSED {{ color: var(--td-green); background: #EAF7EF; }}
    .FAILED {{ color: var(--td-red); background: #FDECEC; }}
    .ERROR {{ color: var(--td-amber); background: #FFF4E2; }}
    .repairs {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .root-cause-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 14px;
      margin-top: 14px;
    }}
    .root-cause-card {{
      border-left: 5px solid var(--td-orange);
      min-width: 0;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .root-cause-title {{
      margin: 0 0 8px;
      font-size: 18px;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .impact-list {{
      margin: 10px 0 0;
      padding-left: 18px;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .root-cause-card p,
    .root-cause-card li {{
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    details {{
      margin-top: 8px;
    }}
    pre {{
      max-height: 220px;
      overflow: auto;
      background: #071D2E;
      color: #EAF2F8;
      padding: 12px;
      border-radius: 6px;
      font-size: 12px;
    }}
    .backend-error {{
      border-left: 4px solid var(--td-orange);
      background: #FFF7F2;
      padding: 10px 12px;
      margin: 8px 0;
      overflow-wrap: anywhere;
    }}
    .next-step {{
      background: #F3F8FC;
      border: 1px solid var(--td-line);
      border-radius: 6px;
      padding: 10px 12px;
      margin: 8px 0;
    }}
    @media (max-width: 900px) {{
      .summary-grid {{ grid-template-columns: 1fr 1fr; }}
      h1 {{ font-size: 28px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span class="brand-mark" aria-hidden="true"></span> Teradata metadata trust
    </div>
    <h1>{_h(run.prefix)} trust report</h1>
    <p>
      Human-readable view of the metadata contract that agents, cookbooks, notebooks
      and applications depend on. JSON remains the source evidence; this report helps
      people see what is healthy, what is broken, and what to fix next.
    </p>
  </header>
  <main>
    <section class="summary-grid" aria-label="Validation summary">
      <div class="panel score">
        <div class="score-value" aria-label="Overall score {score}"><span>{score}</span></div>
        <div>
          <div class="metric-label">Overall metadata trust score</div>
          <p>{_h(_score_message(score))}</p>
        </div>
      </div>
      {_metric("Passed", run.passed_count)}
      {_metric("Failed", run.failed_count)}
      {_metric("Errors", run.error_count)}
      {_metric("Repairs", len(repair_candidates))}
    </section>

    <section class="dimension-grid" aria-label="Dimension scores">
      {_dimension_cards(dimension_scores)}
    </section>

    <section class="repairs" aria-label="Repair posture">
      {safe_auto_panel}
      {approval_panel}
    </section>

    {_root_cause_section(root_cause_groups)}

    <section class="panel" style="margin-top:18px">
      <h2>Validation results</h2>
      <div class="toolbar">
        <select id="statusFilter" aria-label="Filter by status">
          <option value="">All statuses</option>
          <option value="PASSED">Passed</option>
          <option value="FAILED">Failed</option>
          <option value="ERROR">Error</option>
        </select>
        <select id="categoryFilter" aria-label="Filter by category">
          <option value="">All categories</option>
          {_category_options(results)}
        </select>
        <select id="severityFilter" aria-label="Filter by severity">
          <option value="">All severities</option>
          {_severity_options(results)}
        </select>
        <input
          id="searchFilter"
          type="search"
          placeholder="Search test, issue, object, hint"
          aria-label="Search results"
        />
      </div>
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Test</th>
            <th>Category</th>
            <th>Severity</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody id="resultsBody">
          {_result_rows(results, dependency_index)}
        </tbody>
      </table>
    </section>

    <script id="trust-report-data" type="application/json">{data_json}</script>
    <script>
      const filters = ["statusFilter", "categoryFilter", "severityFilter", "searchFilter"];
      function applyFilters() {{
        const status = document.getElementById("statusFilter").value;
        const category = document.getElementById("categoryFilter").value;
        const severity = document.getElementById("severityFilter").value;
        const search = document.getElementById("searchFilter").value.toLowerCase();
        document.querySelectorAll("#resultsBody tr").forEach((row) => {{
          const visible = (!status || row.dataset.status === status)
            && (!category || row.dataset.category === category)
            && (!severity || row.dataset.severity === severity)
            && (!search || row.textContent.toLowerCase().includes(search));
          row.style.display = visible ? "" : "none";
        }});
      }}
      filters.forEach((id) => document.getElementById(id).addEventListener("input", applyFilters));
    </script>
  </main>
</body>
</html>
"""


def _score(results: list[TestResult]) -> int:
    if not results:
        return 100
    total = sum(_weight(result) for result in results)
    earned = sum(_weight(result) for result in results if result.status == TestStatus.PASSED)
    return round((earned / total) * 100) if total else 100


def _dimension_scores(results: list[TestResult]) -> dict[str, int]:
    categories = sorted({result.test_case.category.value for result in results})
    return {
        category: _score(
            [result for result in results if result.test_case.category.value == category]
        )
        for category in categories
    }


def _weight(result: TestResult) -> int:
    return _SEVERITY_WEIGHTS.get(result.test_case.severity, 5)


def _result_sort_key(result: TestResult) -> tuple[int, int, str]:
    status_rank = {TestStatus.ERROR: 0, TestStatus.FAILED: 1, TestStatus.PASSED: 2}
    severity_rank = {
        TestSeverity.CRITICAL: 0,
        TestSeverity.ERROR: 1,
        TestSeverity.WARNING: 2,
        TestSeverity.INFO: 3,
    }
    return (
        status_rank[result.status],
        severity_rank[result.test_case.severity],
        result.test_case.test_id,
    )


def _repair_to_dict(candidate: RepairCandidate) -> dict[str, object]:
    payload = asdict(candidate)
    payload["mode"] = candidate.mode.value
    return payload


def _metric(label: str, value: int) -> str:
    return f"""<div class="panel">
      <div class="metric-label">{_h(label)}</div>
      <div class="metric-value">{value}</div>
    </div>"""


def _dimension_cards(dimension_scores: dict[str, int]) -> str:
    return "\n".join(
        f"""<div class="panel">
          <div class="metric-label">{_h(category)}</div>
          <div class="metric-value">{score}</div>
          <div class="bar" aria-hidden="true"><span style="width:{score}%"></span></div>
        </div>"""
        for category, score in dimension_scores.items()
    )


def _repair_panel(title: str, count: int, text: str) -> str:
    return f"""<div class="panel">
      <div class="metric-label">{_h(title)}</div>
      <div class="metric-value">{count}</div>
      <p>{_h(text)}</p>
    </div>"""


def _root_cause_section(groups: list[dict[str, object]]) -> str:
    if not groups:
        return ""
    cards = "\n".join(_root_cause_card(group) for group in groups)
    return f"""<section class="panel" style="margin-top:18px" aria-label="Root cause groups">
      <h2>Root cause groups</h2>
      <p>
        Repeated failures are grouped by the same missing object, missing column, or capability
        defect so the likely first fix is visible before the detailed validation rows.
      </p>
      <div class="root-cause-grid">
        {cards}
      </div>
    </section>"""


def _root_cause_card(group: dict[str, object]) -> str:
    impacts = group.get("impacts")
    impact_items = ""
    if isinstance(impacts, list):
        impact_items = "\n".join(f"<li>{_h(impact)}</li>" for impact in impacts[:6])
    return f"""<div class="panel root-cause-card">
      <div class="metric-label">{_h(group.get("issue_code", "ROOT_CAUSE"))}</div>
      <h3 class="root-cause-title">{_h(group.get("title", "Shared failure"))}</h3>
      <p><strong>{_h(group.get("impact_count", 0))} downstream failures</strong></p>
      <p>{_h(group.get("next_step", "Review the grouped validation evidence."))}</p>
      <ul class="impact-list">{impact_items}</ul>
    </div>"""


def _category_options(results: list[TestResult]) -> str:
    return "\n".join(
        f'<option value="{_h(category)}">{_h(category.title())}</option>'
        for category in sorted({result.test_case.category.value for result in results})
    )


def _severity_options(results: list[TestResult]) -> str:
    return "\n".join(
        f'<option value="{_h(severity)}">{_h(severity.title())}</option>'
        for severity in sorted({result.test_case.severity.value for result in results})
    )


def _result_rows(
    results: list[TestResult],
    dependency_index: dict[str, list[str]],
) -> str:
    return "\n".join(_result_row(result, dependency_index) for result in results)


def _result_row(
    result: TestResult,
    dependency_index: dict[str, list[str]],
) -> str:
    evidence = _evidence_summary(result)
    next_step = _next_step(result, dependency_index)
    sample_json = _h(json.dumps(result.sample_rows[:3], indent=2, sort_keys=True, default=str))
    error_html = ""
    if result.error_message:
        error_html = f"""<div class="backend-error">
          <strong>Backend error</strong><br>{_h(_concise_backend_error(result.error_message))}
        </div>"""
    next_step_html = ""
    if next_step:
        next_step_html = f"""<div class="next-step">
          <strong>Next step</strong><br>{_h(next_step)}
        </div>"""
    return f"""<tr
      data-status="{result.status.value}"
      data-category="{result.test_case.category.value}"
      data-severity="{result.test_case.severity.value}"
    >
      <td><span class="pill {result.status.value}">{result.status.value}</span></td>
      <td>
        <strong>{_h(result.test_case.name)}</strong><br>
        <code>{_h(result.test_case.test_id)}</code>
      </td>
      <td>{_h(result.test_case.category.value)}</td>
      <td>{_h(result.test_case.severity.value)}</td>
      <td>
        <p>{_h(evidence)}</p>
        {error_html}
        {next_step_html}
        <details>
          <summary>Structured evidence</summary>
          <pre>{sample_json}</pre>
        </details>
      </td>
    </tr>"""


def _evidence_summary(result: TestResult) -> str:
    if result.status == TestStatus.PASSED:
        return "Contract passed."
    if result.sample_rows:
        first = result.sample_rows[0]
        issue_code = first.get("issue_code")
        repair_hint = first.get("repair_hint")
        if issue_code and repair_hint:
            return f"{issue_code}: {repair_hint}"
        if issue_code:
            return str(issue_code)
    return (
        result.error_message
        or result.test_case.repair_strategy
        or "Review the structured evidence."
    )


def _next_step(
    result: TestResult,
    dependency_index: dict[str, list[str]],
) -> str:
    if result.status == TestStatus.PASSED or not result.sample_rows:
        return ""
    first = result.sample_rows[0]
    issue_code = str(first.get("issue_code") or "")
    if issue_code == "MISSING_COLUMN":
        missing_column = str(first.get("missing_column") or "")
        alter_hint = _missing_column_alter_hint(missing_column)
        dependent_hint = _dependent_object_hint(missing_column, first, dependency_index)
        return (
            "Confirm whether the recipe is stale or the source object is missing a required "
            f"column. {alter_hint}{dependent_hint} Then re-run validation."
        )
    if issue_code == "MISSING_OBJECT":
        missing_object = str(first.get("missing_object") or "the missing object")
        return (
            f"Confirm whether {missing_object} was renamed, dropped, or never deployed. "
            "Update the recipe to the current object, deploy the missing object, or quarantine "
            "the recipe until the contract is restored."
        )
    if issue_code == "UNSUPPORTED_CAPABILITY":
        return (
            "Switch to a capability-compatible recipe variant or update the capability metadata "
            "only after the platform feature is genuinely available."
        )
    if issue_code == "STALE_OBJECT_NAME":
        return "Apply the deterministic alias repair or replace the retired object name manually."
    return result.test_case.repair_strategy or "Review the structured evidence and choose a repair."


def _root_cause_groups(
    results: list[TestResult],
    dependency_index: dict[str, list[str]],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for result in results:
        if result.status == TestStatus.PASSED:
            continue
        for sample_row in result.sample_rows:
            root = _root_cause_identity(sample_row)
            if not root:
                continue
            issue_code, root_value = root
            group = groups.setdefault(
                root,
                {
                    "issue_code": issue_code,
                    "root_value": root_value,
                    "title": _root_cause_title(issue_code, root_value),
                    "next_step": _root_cause_next_step(issue_code, root_value, dependency_index),
                    "impacts": [],
                    "impact_count": 0,
                },
            )
            impact = _result_impact_label(result, sample_row)
            if impact not in group["impacts"]:
                group["impacts"].append(impact)
                group["impact_count"] = int(group["impact_count"]) + 1

    repeated_groups = [group for group in groups.values() if int(group["impact_count"]) > 1]
    return sorted(
        repeated_groups,
        key=lambda group: (-int(group["impact_count"]), str(group["title"])),
    )


def _root_cause_identity(sample_row: dict[str, object]) -> tuple[str, str] | None:
    issue_code = str(sample_row.get("issue_code") or "")
    if issue_code == "MISSING_COLUMN":
        missing_column = str(sample_row.get("missing_column") or "")
        return (issue_code, missing_column) if missing_column else None
    if issue_code == "MISSING_OBJECT":
        missing_object = str(sample_row.get("missing_object") or "")
        return (issue_code, missing_object) if missing_object else None
    if issue_code == "UNSUPPORTED_CAPABILITY":
        capability = str(
            sample_row.get("capability") or sample_row.get("unsupported_feature") or ""
        )
        return (issue_code, capability) if capability else None
    if issue_code == "STALE_OBJECT_NAME":
        token = str(sample_row.get("token") or sample_row.get("object_name") or "")
        return (issue_code, token) if token else None
    return None


def _root_cause_title(issue_code: str, root_value: str) -> str:
    labels = {
        "MISSING_COLUMN": "Missing column",
        "MISSING_OBJECT": "Missing object",
        "UNSUPPORTED_CAPABILITY": "Unsupported capability",
        "STALE_OBJECT_NAME": "Stale object name",
    }
    return f"{labels.get(issue_code, issue_code)}: {root_value}"


def _root_cause_next_step(
    issue_code: str,
    root_value: str,
    dependency_index: dict[str, list[str]],
) -> str:
    if issue_code == "MISSING_COLUMN":
        dependent_hint = _dependent_object_hint(root_value, {}, dependency_index)
        return f"{_missing_column_alter_hint(root_value)}{dependent_hint} Then re-run validation."
    if issue_code == "MISSING_OBJECT":
        return (
            f"Confirm whether {root_value} was renamed, dropped, or never deployed. "
            "Deploy it, update metadata to the current object, or quarantine dependent recipes."
        )
    if issue_code == "UNSUPPORTED_CAPABILITY":
        return (
            "Switch impacted recipes to a deployed capability-compatible pattern, or update "
            "capability metadata only after the feature is genuinely available."
        )
    if issue_code == "STALE_OBJECT_NAME":
        return "Apply the deterministic alias repair or replace the retired object name manually."
    return "Review the grouped validation evidence and repair the shared upstream contract."


def _result_impact_label(result: TestResult, sample_row: dict[str, object]) -> str:
    recipe_id = sample_row.get("recipe_id")
    recipe_title = sample_row.get("recipe_title")
    if recipe_id and recipe_title:
        return f"{recipe_id}: {recipe_title}"
    if recipe_id:
        return str(recipe_id)
    object_names = _dependent_objects_from_sample(sample_row)
    if object_names:
        return ", ".join(object_names)
    return f"{result.test_case.test_id}: {result.test_case.name}"


def _dependency_index(results: list[TestResult]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for result in results:
        if result.status == TestStatus.PASSED:
            continue
        for sample_row in result.sample_rows:
            missing_column = str(sample_row.get("missing_column") or "")
            if not missing_column:
                continue
            for object_name in _dependent_objects_from_sample(sample_row):
                index.setdefault(missing_column, [])
                if object_name not in index[missing_column]:
                    index[missing_column].append(object_name)
    return index


def _dependent_object_hint(
    missing_column: str,
    sample_row: dict[str, object],
    dependency_index: dict[str, list[str]],
) -> str:
    dependent_objects = [
        object_name
        for object_name in (
            [
                *dependency_index.get(missing_column, []),
                *_dependent_objects_from_sample(sample_row),
            ]
        )
        if object_name
    ]
    deduplicated = list(dict.fromkeys(dependent_objects))
    if not deduplicated:
        return " Refresh dependent views or metadata."
    return " Recreate or test these dependent objects first: " + ", ".join(deduplicated) + "."


def _dependent_objects_from_sample(sample_row: dict[str, object]) -> list[str]:
    objects: list[str] = []
    database_name = sample_row.get("database_name")
    view_name = sample_row.get("view_name")
    if database_name and view_name:
        objects.append(f"{database_name}.{view_name}")
    dependency_fields = (
        "dependent_views",
        "affected_views",
        "dependent_objects",
        "affected_objects",
    )
    for field_name in dependency_fields:
        field_value = sample_row.get(field_name)
        if isinstance(field_value, str):
            objects.extend(_split_object_list(field_value))
        elif isinstance(field_value, list):
            objects.extend(str(value) for value in field_value if value)
    return list(dict.fromkeys(objects))


def _split_object_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _missing_column_alter_hint(missing_column: str) -> str:
    parts = missing_column.split(".")
    if len(parts) >= 3:
        table_name = ".".join(parts[-3:-1])
        column_name = parts[-1]
        return (
            f"If the column is required, add it with an explicit data type, for example "
            f"ALTER TABLE {table_name} ADD {column_name} <data_type>."
        )
    if missing_column:
        return (
            f"If the column is required, add {missing_column} with an explicit data type to the "
            "source table."
        )
    return "If the column is required, add it with an explicit data type to the source table."


def _concise_backend_error(error_message: str) -> str:
    message = error_message.strip()
    for marker in ("\n at ", ". at ", " at gosqldriver/", " at database/sql."):
        if marker in message:
            message = message.split(marker, maxsplit=1)[0]
            break
    if message.startswith("RuntimeError:"):
        message = message.removeprefix("RuntimeError:").strip()
    return message.rstrip(".") + "."


def _score_message(score: int) -> str:
    if score >= 90:
        return "Metadata contract is healthy. Review remaining warnings and keep evidence current."
    if score >= 70:
        return (
            "Metadata is usable with targeted fixes. Resolve critical failures before broad agent "
            "use."
        )
    return (
        "Metadata trust is low. Prioritise critical failures and safe repairs before relying on "
        "generated SQL."
    )


def _json_for_html(payload: dict[str, object]) -> str:
    return _h(json.dumps(payload, sort_keys=True, default=str))


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)
