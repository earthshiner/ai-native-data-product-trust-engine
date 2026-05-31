"""Standalone HTML report rendering for human trust review."""

from __future__ import annotations

import base64
import html
import json
from dataclasses import asdict
from importlib import resources
from pathlib import Path

from ai_native_data_product_trust_engine.error_formatting import concise_backend_error
from ai_native_data_product_trust_engine.models import (
    TestResult,
    TestSeverity,
    TestStatus,
    ValidationRun,
)
from ai_native_data_product_trust_engine.repairs import RepairCandidate
from ai_native_data_product_trust_engine.reports import (
    format_duration,
    validation_run_duration_seconds,
    validation_run_to_dict,
)
from ai_native_data_product_trust_engine.scoring import (
    dimension_scores as calculate_dimension_scores,
    scorecards as calculate_scorecards,
)

_TERM_DEFINITIONS = {
    "CAPABILITY": (
        "Checks whether metadata and recipes only claim platform features that the deployed "
        "product actually supports."
    ),
    "DATA_QUALITY": (
        "Targeted evidence checks that prove declared data-product metadata claims, not broad "
        "raw data profiling."
    ),
    "FREE_TEXT": (
        "Checks descriptions, glossary text and cookbook notes for stale object names, typos "
        "and unsupported capability references."
    ),
    "OPERATIONAL": (
        "Checks freshness, observability, monitoring, SLA and pipeline-health evidence."
    ),
    "PERFORMANCE": (
        "Checks execution-readiness risks such as skew, missing statistics, expensive joins "
        "and unbounded recipes."
    ),
    "QUERY": (
        "Checks cookbook SQL templates, parameters, EXPLAIN validation and expected result "
        "shape."
    ),
    "SEMANTIC": (
        "Checks product meaning: entities, columns, relationships, paths, glossary, policy "
        "and design metadata."
    ),
    "STRUCTURAL": (
        "Checks deployed databases, tables, views, columns, datatypes and view contracts."
    ),
    "REPAIRS": (
        "Metadata repair candidates generated from failed checks. Safe-auto repairs are "
        "deterministic; approval-required repairs need steward judgement."
    ),
    "DATA_PRODUCT_TRUST": (
        "A score for whether the product is correctly described, governed and safe for "
        "agents to use."
    ),
    "PERFORMANCE_READINESS": (
        "A separate score for whether expected access paths are likely to run efficiently."
    ),
    "OPERATIONAL_READINESS": (
        "A separate score for whether run-state signals such as freshness and monitoring "
        "are healthy."
    ),
}

_HEADER_IMAGE_PACKAGE = "ai_native_data_product_trust_engine.assets"
_HEADER_IMAGE_NAME = "orange_blue_gradient.png"

_ISSUE_CONSEQUENCES = {
    "BUS_VIEW_SELECTS_TABLE_DIRECTLY": (
        "Business consumers may bypass the standard access contract, including locking, column "
        "order and table/view separation guarantees."
    ),
    "BUSINESS_LOGIC_IN_STD_VIEW": (
        "The standard access layer may stop being a predictable 1:1 table contract, making "
        "generated SQL and downstream view assumptions less reliable."
    ),
    "COLUMN_TYPE_DRIFT": (
        "Generated joins and filters may trigger implicit casts, poor plans or incorrect "
        "comparisons across similar business keys."
    ),
    "DIRECT_TABLE_VIEW_MISSING_LOCK": (
        "Queries may take stronger locks than intended or interfere with concurrent product "
        "loads and readers."
    ),
    "MEMORY_DATABASE_NOT_DEPLOYED": (
        "Agents may be unable to read glossary, cookbook or design-memory guidance for the "
        "product."
    ),
    "MEMORY_DATABASE_NOT_IN_MODULE_MAP": (
        "Agents may miss available Memory metadata or navigate to the wrong module database."
    ),
    "MISSING_APPROVED_ENTRYPOINT": (
        "Agents cannot determine the governed access path and may guess a table or view that "
        "bypasses policy."
    ),
    "MISSING_COLUMN": (
        "Generated SQL, views or recipes that depend on this column may fail at runtime."
    ),
    "MISSING_CONTRACT_URI": (
        "Clients may query data before understanding the product contract, scope and usage "
        "rules."
    ),
    "MISSING_DATA_PRODUCT_REGISTRY_TABLE": (
        "MCP clients may not have a product-first discovery anchor and may fall back to guessing "
        "databases or tables."
    ),
    "MISSING_JOIN_COLUMN_STATS": (
        "Optimizer plans for generated relationship joins may be slower or less stable."
    ),
    "MISSING_OBJECT": (
        "Generated SQL or published views may reference objects that no longer exist."
    ),
    "MISSING_ORIENTATION_MANIFEST": (
        "Agents may not know which metadata, policy, quality and access resources to read before "
        "querying data."
    ),
    "MISSING_POLICY_URI": (
        "Clients may not see access rules or entitlements before attempting data access."
    ),
    "MISSING_PRODUCT_REGISTRY_ROW": (
        "The product may be invisible to product-first discovery even if its tables and views "
        "exist."
    ),
    "MISSING_SEMANTIC_URI": (
        "Agents may not be able to locate the semantic model and may generate SQL from physical "
        "schemas alone."
    ),
    "MISSING_STANDARD_LOCKING_VIEW": (
        "Agents and applications may query product tables directly, potentially taking locks "
        "that block other queries from using those tables."
    ),
    "MISSING_VIEW_COLUMN_LIST": (
        "The view output contract may drift silently if the underlying table changes."
    ),
    "NO_PRODUCT_VIEWS_FOUND": (
        "The product may not expose a usable governed view layer for agents or applications."
    ),
    "OBSERVABILITY_DATABASE_NOT_IN_MODULE_MAP": (
        "Agents may miss lineage, quality or usage evidence when assessing operational readiness."
    ),
    "SELECT_STAR": (
        "Column order and shape may change when source tables evolve, breaking generated SQL "
        "contracts."
    ),
    "SEMANTIC_DATABASE_NOT_DEPLOYED": (
        "The registry points to a Semantic database that cannot be found, so metadata discovery "
        "may fail."
    ),
    "SEMANTIC_DATABASE_NOT_IN_MODULE_MAP": (
        "The registry and module map disagree, so agents may navigate inconsistent metadata "
        "locations."
    ),
    "STALE_OBJECT_NAME": (
        "Agents may copy retired object names into generated SQL or documentation."
    ),
    "STD_VIEW_COLUMN_ORDER_MISMATCH": (
        "The access view may no longer be a faithful 1:1 table projection, which can confuse "
        "agents and consumers."
    ),
    "TABLE_AMP_SKEW": (
        "Large scans or joins may be uneven across AMPs, increasing runtime and resource pressure."
    ),
    "UNSUPPORTED_CAPABILITY": (
        "Agents may choose recipes or functions that the deployed platform/product cannot run."
    ),
}

_CATEGORY_CONSEQUENCES = {
    "CAPABILITY": (
        "Agents may select behaviours or recipes that are not supported by the deployed product."
    ),
    "DATA_QUALITY": (
        "Trust evidence for declared data rules may be incomplete or misleading."
    ),
    "FREE_TEXT": (
        "Agents may learn stale names, ambiguous concepts or unsupported patterns from metadata "
        "descriptions."
    ),
    "OPERATIONAL": (
        "Freshness, monitoring or pipeline health may be unknown when consumers decide whether "
        "to use the product."
    ),
    "PERFORMANCE": (
        "The product may still be trustworthy, but generated access paths could be slow or "
        "resource-intensive."
    ),
    "QUERY": (
        "Generated or cookbook SQL may fail, return unexpected shapes or use unsafe access paths."
    ),
    "SEMANTIC": (
        "Agents may misunderstand product meaning, ownership, policies or approved navigation."
    ),
    "STRUCTURAL": (
        "Published objects may not match the contract that agents and applications depend on."
    ),
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
    scorecards = calculate_scorecards(results)
    dimension_scores = calculate_dimension_scores(results)
    duration = format_duration(validation_run_duration_seconds(run))
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
            "scores": scorecards,
            "dimension_scores": dimension_scores,
            "root_cause_groups": root_cause_groups,
        }
    )
    header_image = _header_image_data_uri()

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
      background-color: var(--td-navy);
      background-image: linear-gradient(90deg, rgba(0, 35, 60, 0.90), rgba(0, 35, 60, 0.54)), url("{header_image}");
      background-position: center;
      background-size: cover;
      color: var(--td-white);
      padding: 34px 32px 42px;
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
    .score-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
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
    .tabs {{
      display: flex;
      gap: 8px;
      overflow-x: auto;
      border-bottom: 1px solid var(--td-line);
      margin-bottom: 18px;
    }}
    .tab {{
      appearance: none;
      border: 0;
      border-bottom: 3px solid transparent;
      background: transparent;
      color: var(--td-muted);
      cursor: pointer;
      font: inherit;
      font-weight: 700;
      padding: 12px 14px;
      white-space: nowrap;
    }}
    .tab[aria-selected="true"] {{
      border-bottom-color: var(--td-orange);
      color: var(--td-navy);
    }}
    .tab:focus-visible {{
      outline: 3px solid rgba(255, 95, 2, 0.35);
      outline-offset: 2px;
    }}
    .tab-panel[hidden] {{
      display: none;
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
      background: #D9E2EA;
      position: relative;
      font-size: 28px;
      font-weight: 700;
    }}
    .score-value.not-assessed {{
      background: #D9E2EA;
      color: var(--td-white);
      font-size: 24px;
      text-align: center;
      line-height: 1.1;
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
    .score-body {{
      min-width: 0;
    }}
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
    .term {{
      cursor: help;
      text-decoration: underline dotted;
      text-underline-offset: 3px;
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
    .glossary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
      margin-top: 12px;
    }}
    .glossary-item {{
      border-top: 3px solid var(--td-orange);
      padding-top: 10px;
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
    .consequence {{
      background: #FFF8EB;
      border: 1px solid #FFD8A8;
      border-left: 4px solid var(--td-amber);
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
      Human-readable view of the product trust, performance readiness, and operational
      readiness signals that agents, cookbooks, notebooks and applications depend on.
      JSON remains the source evidence; this report helps people see what is healthy,
      what is broken, and what to fix next.
    </p>
  </header>
  <main>
    <nav class="tabs" role="tablist" aria-label="Report sections">
      {_tab_button("overview", "Overview", True)}
      {_tab_button("root-causes", "Root causes", False)}
      {_tab_button("repairs", "Repairs", False)}
      {_tab_button("glossary", "Glossary", False)}
      {_tab_button("results", "Validation results", False)}
    </nav>

    <section
      id="panel-overview"
      class="tab-panel"
      role="tabpanel"
      aria-labelledby="tab-overview"
    >
      <section class="score-grid" aria-label="Readiness scores">
        {_score_card(
            "Data product trust score",
            "DATA_PRODUCT_TRUST",
            scorecards["data_product_trust"],
            "Measures metadata, semantics, contracts, access safety and data-trust evidence.",
        )}
        {_score_card(
            "Performance readiness score",
            "PERFORMANCE_READINESS",
            scorecards["performance_readiness"],
            "Measures execution risk such as skew, statistics, expensive joins and recipe bounds.",
        )}
        {_score_card(
            "Operational readiness score",
            "OPERATIONAL_READINESS",
            scorecards["operational_readiness"],
            "Measures freshness, observability, monitoring, SLA and pipeline health signals.",
        )}
      </section>

      <section class="summary-grid" aria-label="Validation summary">
        {_metric("Passed", run.passed_count)}
        {_metric("Failed", run.failed_count)}
        {_metric("Errors", run.error_count)}
        {_metric("Repairs", len(repair_candidates))}
        {_metric("Duration", duration)}
      </section>

      <section class="dimension-grid" aria-label="Dimension scores">
        {_dimension_cards(dimension_scores)}
      </section>
    </section>

    <section
      id="panel-root-causes"
      class="tab-panel"
      role="tabpanel"
      aria-labelledby="tab-root-causes"
      hidden
    >
      {_root_cause_section(root_cause_groups)}
    </section>

    <section
      id="panel-repairs"
      class="tab-panel"
      role="tabpanel"
      aria-labelledby="tab-repairs"
      hidden
    >
      <section class="repairs" aria-label="Repair posture">
        {safe_auto_panel}
        {approval_panel}
      </section>
    </section>

    <section
      id="panel-glossary"
      class="tab-panel"
      role="tabpanel"
      aria-labelledby="tab-glossary"
      hidden
    >
      {_glossary_section()}
    </section>

    <section
      id="panel-results"
      class="tab-panel"
      role="tabpanel"
      aria-labelledby="tab-results"
      hidden
    >
      <section class="panel">
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
      document.querySelectorAll("[role='tab']").forEach((tab) => {{
        tab.addEventListener("click", () => {{
          const targetPanelId = tab.getAttribute("aria-controls");
          document.querySelectorAll("[role='tab']").forEach((item) => {{
            item.setAttribute("aria-selected", String(item === tab));
          }});
          document.querySelectorAll(".tab-panel").forEach((panel) => {{
            panel.hidden = panel.id !== targetPanelId;
          }});
        }});
      }});
    </script>
  </main>
</body>
</html>
"""


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


def _metric(label: str, value: object) -> str:
    return f"""<div class="panel">
      <div class="metric-label">{_term(label.upper(), label)}</div>
      <div class="metric-value">{value}</div>
    </div>"""


def _tab_button(tab_id: str, label: str, selected: bool) -> str:
    selected_value = "true" if selected else "false"
    return f"""<button
        id="tab-{_h(tab_id)}"
        class="tab"
        type="button"
        role="tab"
        aria-selected="{selected_value}"
        aria-controls="panel-{_h(tab_id)}"
      >{_h(label)}</button>"""


def _score_card(
    label: str,
    term_key: str,
    score_summary: dict[str, object],
    description: str,
) -> str:
    assessed = bool(score_summary.get("assessed"))
    score = score_summary.get("score")
    message = str(score_summary.get("message") or "")
    test_count = int(score_summary.get("test_count") or 0)
    if assessed and isinstance(score, int):
        score_html = (
            f"""<div class="score-value" style="background: conic-gradient(var(--td-orange) """
            f"""{score}%, #D9E2EA {score}%);" aria-label="{_h(label)} {score}">"""
            f"""<span>{score}</span></div>"""
        )
    else:
        score_html = (
            f"""<div class="score-value not-assessed" aria-label="{_h(label)} not assessed">"""
            """<span>N/A</span></div>"""
        )
    return f"""<div class="panel score">
      {score_html}
      <div class="score-body">
        <div class="metric-label">{_term(term_key, label)}</div>
        <p>{_h(description)}</p>
        <p>{_h(message)}</p>
        <p><strong>{test_count}</strong> checks in this score.</p>
      </div>
    </div>"""


def _dimension_cards(dimension_scores: dict[str, int]) -> str:
    return "\n".join(
        f"""<div class="panel">
          <div class="metric-label">{_term(category, category)}</div>
          <div class="metric-value">{score}</div>
          <div class="bar" aria-hidden="true"><span style="width:{score}%"></span></div>
        </div>"""
        for category, score in dimension_scores.items()
    )


def _repair_panel(title: str, count: int, text: str) -> str:
    return f"""<div class="panel">
      <div class="metric-label">{_term("REPAIRS", title)}</div>
      <div class="metric-value">{count}</div>
      <p>{_h(text)}</p>
    </div>"""


def _glossary_section() -> str:
    terms = (
        "DATA_PRODUCT_TRUST",
        "PERFORMANCE_READINESS",
        "OPERATIONAL_READINESS",
        "STRUCTURAL",
        "SEMANTIC",
        "QUERY",
        "CAPABILITY",
        "FREE_TEXT",
        "DATA_QUALITY",
        "PERFORMANCE",
        "OPERATIONAL",
        "REPAIRS",
    )
    cards = "\n".join(
        f"""<div class="glossary-item">
          <div class="metric-label">{_h(term.replace("_", " ").title())}</div>
          <p>{_h(_TERM_DEFINITIONS[term])}</p>
        </div>"""
        for term in terms
    )
    return f"""<section class="panel" aria-label="Glossary">
      <h2>Glossary</h2>
      <div class="glossary-grid">
        {cards}
      </div>
    </section>"""


def _root_cause_section(groups: list[dict[str, object]]) -> str:
    if not groups:
        return """<section class="panel" aria-label="Root cause groups">
      <h2>Root cause groups</h2>
      <p>No repeated root cause groups detected.</p>
    </section>"""
    cards = "\n".join(_root_cause_card(group) for group in groups)
    return f"""<section class="panel" aria-label="Root cause groups">
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
    consequence = _consequence(result)
    next_step = _next_step(result, dependency_index)
    sample_json = _h(json.dumps(result.sample_rows[:3], indent=2, sort_keys=True, default=str))
    error_html = ""
    if result.error_message:
        error_html = f"""<div class="backend-error">
          <strong>Backend error</strong><br>{_h(concise_backend_error(result.error_message))}
        </div>"""
    next_step_html = ""
    if next_step:
        next_step_html = f"""<div class="next-step">
          <strong>Next step</strong><br>{_h(next_step)}
        </div>"""
    consequence_html = ""
    if consequence:
        consequence_html = f"""<div class="consequence">
          <strong>Potential consequence</strong><br>{_h(consequence)}
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
      <td>{_term(result.test_case.category.value, result.test_case.category.value)}</td>
      <td>{_h(result.test_case.severity.value)}</td>
      <td>
        <p>{_h(evidence)}</p>
        {error_html}
        {consequence_html}
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
    if result.error_message:
        return concise_backend_error(result.error_message)
    return result.test_case.repair_strategy or "Review the structured evidence."


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


def _consequence(result: TestResult) -> str:
    if result.status == TestStatus.PASSED:
        return ""
    issue_code = ""
    if result.sample_rows:
        issue_code = str(result.sample_rows[0].get("issue_code") or "")
    if issue_code in _ISSUE_CONSEQUENCES:
        return _ISSUE_CONSEQUENCES[issue_code]
    category = result.test_case.category.value
    if category in _CATEGORY_CONSEQUENCES:
        return _CATEGORY_CONSEQUENCES[category]
    if result.error_message:
        return (
            "Validation could not complete for this check, so the product's current contract "
            "health is unknown until the backend error is resolved."
        )
    return (
        "Consumers may receive incomplete or misleading trust evidence until this validation "
        "failure is reviewed."
    )


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


def _json_for_html(payload: dict[str, object]) -> str:
    return _h(json.dumps(payload, sort_keys=True, default=str))


def _header_image_data_uri() -> str:
    image = resources.files(_HEADER_IMAGE_PACKAGE).joinpath(_HEADER_IMAGE_NAME).read_bytes()
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _term(term_key: str, label: str) -> str:
    definition = _TERM_DEFINITIONS.get(term_key)
    if not definition:
        return _h(label)
    return f"""<span class="term" title="{_h(definition)}">{_h(label)}</span>"""


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)
