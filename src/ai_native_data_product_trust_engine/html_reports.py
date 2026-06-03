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
    ExcludedCheck,
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
_HEADER_IMAGE_NAME = "orange_blue_gradient.jpg"
_LOGO_IMAGE_NAME = "teradata_logo_rgb_pos.png"

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
    "CARDINALITY_SOURCE_NOT_UNIQUE": (
        "Agents may trust a one-side relationship that actually duplicates source keys, causing "
        "duplicate-heavy or misleading joins."
    ),
    "CARDINALITY_TARGET_NOT_UNIQUE": (
        "Agents may trust a one-side relationship that actually duplicates target keys, causing "
        "duplicate-heavy or misleading joins."
    ),
    "CURRENT_VIEW_MISSING_CURRENT_FILTER": (
        "Agents may query historical rows when they expect the current-state view to expose only "
        "current records."
    ),
    "CURRENT_VIEW_NOT_DECLARED": (
        "Agents may not know the governed current-state access path for temporal records."
    ),
    "CURRENT_VIEW_NOT_DEPLOYED": (
        "Agents may navigate to a declared current-state view that does not exist."
    ),
    "DIRECT_TABLE_VIEW_MISSING_LOCK": (
        "Queries may take stronger locks than intended or interfere with concurrent product "
        "loads and readers."
    ),
    "DUPLICATE_CURRENT_RECORD": (
        "Current-state queries may return multiple active records for the same business key."
    ),
    "EXPLAIN_ALL_AMP_SCAN": (
        "Interactive or agent-generated access may scan more data than intended, increasing "
        "runtime and resource pressure."
    ),
    "EXPLAIN_DUPLICATED_LARGE_TABLE": (
        "The plan may duplicate large data across AMPs, increasing spool, network and runtime "
        "risk."
    ),
    "EXPLAIN_LOW_CONFIDENCE": (
        "The optimiser estimates may be unreliable, so generated SQL performance may vary "
        "significantly."
    ),
    "EXPLAIN_MISSING_STATS": (
        "The optimiser may choose a poor plan because required statistics are missing or stale."
    ),
    "EXPLAIN_PRODUCT_JOIN": (
        "The recipe may combine rows without a selective join path, causing excessive work or "
        "unexpected result expansion."
    ),
    "JOIN_COLUMN_CHARSET_MISMATCH": (
        "Generated relationship joins may require character-set conversion, increasing plan "
        "risk and making comparisons less predictable."
    ),
    "JOIN_COLUMN_LENGTH_MISMATCH": (
        "Generated relationship joins may truncate, pad or cast join keys, causing poor plans "
        "or missed matches."
    ),
    "JOIN_COLUMN_PRECISION_SCALE_MISMATCH": (
        "Generated relationship joins may cast numeric keys or compare rounded values, causing "
        "plan instability or incorrect matches."
    ),
    "JOIN_COLUMN_TYPE_MISMATCH": (
        "Generated relationship joins may rely on implicit casts, causing redistribution, poor "
        "plans or failed SQL."
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
    "MISSING_OBSERVABILITY_MODULE": (
        "The product has no active operational evidence anchor for lineage, freshness, quality "
        "or usage monitoring."
    ),
    "MISSING_OBSERVABILITY_SEMANTIC_VIEW": (
        "Agents may not have a governed Semantic view for lineage or latest-run operational "
        "status."
    ),
    "MISSING_OBSERVABILITY_TABLE": (
        "Operational evidence such as quality metrics, lineage definitions or lineage runs may "
        "not be captured."
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
    "NESTED_ORDERED_ANALYTIC": (
        "The recipe uses an ordered analytic calculation inside another analytic calculation, "
        "which Teradata rejects during EXPLAIN and execution."
    ),
    "NO_PRODUCT_VIEWS_FOUND": (
        "The product may not expose a usable governed view layer for agents or applications."
    ),
    "OBSERVABILITY_DATABASE_NOT_IN_MODULE_MAP": (
        "Agents may miss lineage, quality or usage evidence when assessing operational readiness."
    ),
    "OBSERVABILITY_DATABASE_NOT_DEPLOYED": (
        "Operational evidence cannot be recorded or inspected because the registered "
        "Observability database is unavailable."
    ),
    "PRIMARY_INDEX_LOW_CARDINALITY_SUSPECT": (
        "Rows may concentrate on a small number of AMPs if the primary index has few distinct "
        "values."
    ),
    "PRIMARY_INDEX_NOT_DEFINED": (
        "The table may rely on NoPI behaviour, making load/query distribution risk harder for "
        "agents to reason about unless it is intentional and documented."
    ),
    "PRIMARY_INDEX_NULLABLE_COLUMN": (
        "NULL-heavy primary index values may concentrate rows and increase redistribution risk."
    ),
    "PRIMARY_INDEX_SKEW_HIGH": (
        "The observed distribution suggests the primary index may be creating uneven AMP storage."
    ),
    "SELECT_STAR": (
        "Column order and shape may change when source tables evolve, breaking generated SQL "
        "contracts."
    ),
    "SOURCE_TO_TARGET_ORPHAN": (
        "Generated joins may silently drop source records because their declared target key does "
        "not exist."
    ),
    "SEMANTIC_DATABASE_NOT_DEPLOYED": (
        "The registry points to a Semantic database that cannot be found, so metadata discovery "
        "may fail."
    ),
    "SEMANTIC_DATABASE_NOT_IN_MODULE_MAP": (
        "The registry and module map disagree, so agents may navigate inconsistent metadata "
        "locations."
    ),
    "SEMANTIC_SEARCH_CAPABILITY_UNAVAILABLE": (
        "Agents may advertise or select semantic-search behaviour that the deployed product "
        "cannot support."
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
    "TARGET_TO_SOURCE_ORPHAN": (
        "Generated traversals may find target records that are not represented from the declared "
        "source side."
    ),
    "UNSUPPORTED_CAPABILITY": (
        "Agents may choose recipes or functions that the deployed platform/product cannot run."
    ),
    "UNBOUNDED_INTERACTIVE_RECIPE": (
        "Agents may run open-ended queries over large tables, causing slow responses, high "
        "resource use or accidental broad data access."
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
    last_run_at = run.completed_at
    dependency_index = _dependency_index(results)
    root_cause_groups = _root_cause_groups(results, dependency_index)
    attention_objects = _attention_objects(results, dependency_index)
    object_issues = _object_issue_groups(results)
    safe_auto_count = sum(1 for candidate in repair_candidates if not candidate.requires_approval)
    approval_count = sum(1 for candidate in repair_candidates if candidate.requires_approval)
    total_checks = len(results)
    status_equation = (
        f"{run.passed_count} passed + {run.failed_count} failed + {run.error_count} errors "
        f"= {total_checks} total checks"
    )
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
            "attention_objects": attention_objects,
            "object_issues": object_issues,
        }
    )
    header_image = _header_image_data_uri()
    logo_image = _logo_image_data_uri()

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_h(run.prefix)} metadata trust report</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link
    href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&amp;display=swap"
    rel="stylesheet"
  />
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
      position: relative;
      background-color: var(--td-navy);
      background-image:
        linear-gradient(
          100deg,
          rgba(0, 35, 60, 0.94) 0%,
          rgba(0, 35, 60, 0.80) 34%,
          rgba(0, 35, 60, 0.42) 64%,
          rgba(0, 35, 60, 0.18) 100%
        ),
        url("{header_image}");
      background-repeat: no-repeat;
      background-position: right top;
      background-size: cover;
      color: var(--td-white);
      border-bottom: 5px solid var(--td-orange);
    }}
    .header-inner {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 30px 24px 38px;
    }}
    .header-top {{
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 22px;
    }}
    .header-logo {{
      height: 26px;
      width: auto;
      /* official positive wordmark rendered white for the dark gradient */
      filter: brightness(0) invert(1);
    }}
    .header-eyebrow {{
      padding-left: 16px;
      border-left: 1px solid rgba(255, 255, 255, 0.35);
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: #CDDDEA;
    }}
    h1 {{
      margin: 0;
      font-size: 38px;
      font-weight: 300;
      letter-spacing: -0.01em;
      line-height: 1.1;
    }}
    .header-lede {{
      max-width: 720px;
      margin: 12px 0 0;
      font-size: 15px;
      color: #DDE8F0;
    }}
    .header-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 22px;
    }}
    .meta-chip {{
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 6px 12px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.10);
      border: 1px solid rgba(255, 255, 255, 0.22);
      font-size: 12px;
      font-weight: 600;
      color: var(--td-white);
      backdrop-filter: blur(2px);
    }}
    .meta-chip b {{
      font-weight: 700;
    }}
    .meta-chip .meta-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--td-orange);
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
    .overview-section {{
      margin-bottom: 18px;
    }}
    .overview-section h2 {{
      margin: 0 0 8px;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .overview-note {{
      margin: 0 0 14px;
      color: var(--td-muted);
      max-width: 980px;
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
    .metric-suffix {{
      color: var(--td-muted);
      font-size: 16px;
      font-weight: 700;
      margin-left: 2px;
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
    .reference-context {{
      background: #F7F9FB;
      border: 1px solid var(--td-line);
      border-left: 4px solid var(--td-navy);
      border-radius: 6px;
      padding: 10px 12px;
      margin: 8px 0;
      overflow-wrap: anywhere;
    }}
    .inspection-scope {{
      background: #F7F9FB;
      border: 1px solid var(--td-line);
      border-left: 4px solid var(--td-orange);
      border-radius: 6px;
      padding: 10px 12px;
      margin: 8px 0;
      overflow-wrap: anywhere;
    }}
    .inspection-scope ul {{
      margin: 6px 0 0;
      padding-left: 18px;
    }}
    .attention-objects {{
      margin-bottom: 18px;
    }}
    .object-issues {{
      margin-bottom: 18px;
    }}
    .attention-objects h3,
    .object-issues h3 {{
      margin: 0 0 8px;
      font-size: 16px;
    }}
    .attention-objects p,
    .object-issues p {{
      margin: 0 0 12px;
      color: var(--td-muted);
    }}
    .attention-objects td,
    .object-issues td {{
      vertical-align: top;
    }}
    .object-issue-toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 10px 0 12px;
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
      .header-inner {{ padding: 24px 18px 30px; }}
      h1 {{ font-size: 28px; }}
      .header-lede {{ font-size: 14px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <div class="header-top">
        <img class="header-logo" src="{logo_image}" alt="Teradata" />
        <span class="header-eyebrow">Metadata trust report</span>
      </div>
      <h1>{_h(run.prefix)} trust report</h1>
      <p class="header-lede">
        A human-readable view of the product trust, performance readiness and operational
        readiness signals that agents, cookbooks, notebooks and applications depend on. JSON
        remains the source evidence; this report shows what is healthy, what is broken, and
        what to fix next.
      </p>
      <div class="header-meta">
        <span class="meta-chip"><span class="meta-dot" aria-hidden="true"></span>Product <b>{_h(run.prefix)}</b></span>
        <span class="meta-chip"><b>{total_checks}</b>&nbsp;checks carried out</span>
        <span class="meta-chip"><b>{run.passed_count}</b>&nbsp;passed&nbsp;&middot;&nbsp;<b>{run.failed_count}</b>&nbsp;failed&nbsp;&middot;&nbsp;<b>{run.error_count}</b>&nbsp;errors</span>
        <span class="meta-chip">Last run <b>{_h(last_run_at)}</b></span>
        <span class="meta-chip">Run duration <b>{_h(duration)}</b></span>
      </div>
    </div>
  </header>
  <main>
    <nav class="tabs" role="tablist" aria-label="Report sections">
      {_tab_button("overview", "Overview", True)}
      {_tab_button("results", "Validation Results", False)}
      {_tab_button("root-causes", "Root Causes", False)}
      {_tab_button("repairs", "Repairs", False)}
      {_tab_button("checks", "Checks", False)}
      {_tab_button("glossary", "Glossary", False)}
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

      <section class="panel overview-section" aria-label="Overview explanation">
        <h2>How to read the numbers</h2>
        <p class="overview-note">
          Validation outcomes are counts of checks. {_h(status_equation)}. Readiness scorecards
          each count the checks that belong to that score family, and category scores are 0-100
          scores, not check counts. Repair candidates are separate actions derived from failures,
          so they do not add to the validation total.
        </p>
      </section>

      <section class="overview-section" aria-label="Validation outcomes">
        <h2>Validation outcomes</h2>
        <p class="overview-note">These counts reconcile: passed + failed + errors = total checks.</p>
        <div class="summary-grid">
          {_metric("Total checks", total_checks, "Every validation check that ran in this report.")}
          {_metric("Passed", run.passed_count, "Checks with no failed evidence.")}
          {_metric("Failed", run.failed_count, "Checks that returned failed evidence rows.")}
          {_metric("Errors", run.error_count, "Checks that could not complete because the backend returned an error.")}
          {_metric("Excluded", len(run.excluded_checks), "Checks or scanner families skipped by rule configuration.")}
          {_metric("Duration", duration, "Elapsed wall-clock time for this validation run.")}
        </div>
      </section>

      <section class="overview-section" aria-label="Repair candidate summary">
        <h2>Repair candidates</h2>
        <p class="overview-note">
          Repairs are candidate actions generated from failed checks. They are not extra checks and
          are not included in the validation total.
        </p>
        <div class="summary-grid">
          {_metric("Total repairs", len(repair_candidates), "Repair candidates generated from failed/error evidence. See the Repairs tab; CLI runs also write .repairs.md and .repairs.sql beside the JSON report.")}
          {_metric("Safe-auto", safe_auto_count, "Deterministic repair candidates that do not require steward approval.")}
          {_metric("Approval required", approval_count, "Repair candidates that need human judgement before metadata changes.")}
        </div>
      </section>

      <section class="overview-section" aria-label="Category scores">
        <h2>Category scores</h2>
        <p class="overview-note">
          These are per-category scores from 0 to 100. They help identify weak areas, but they are
          not counts and should not be added together.
        </p>
        <div class="dimension-grid">
          {_dimension_cards(dimension_scores)}
        </div>
      </section>
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
        {_object_issue_section(object_issues)}
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
      <section class="panel">
        <h2>Checks excluded by configuration</h2>
        {_excluded_check_table(run.excluded_checks)}
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
      <section class="panel" aria-label="Repair candidate details">
        <h2>Repair candidates</h2>
        <p>
          Repair candidates are generated from failed/error evidence. They are embedded in this
          HTML report data, and the CLI writes the same repair set as sibling
          <code>.repairs.md</code> and <code>.repairs.sql</code> files beside the JSON report.
        </p>
        <section class="repairs" aria-label="Repair posture">
          {safe_auto_panel}
          {approval_panel}
        </section>
        {_repair_candidate_table(repair_candidates)}
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
      id="panel-checks"
      class="tab-panel"
      role="tabpanel"
      aria-labelledby="tab-checks"
      hidden
    >
      <section class="panel">
        <h2>Checks carried out</h2>
        <table>
          <thead>
            <tr>
              <th>Check</th>
              <th>Category</th>
              <th>Severity</th>
              <th>Status</th>
              <th>What is tested</th>
            </tr>
          </thead>
          <tbody>
            {_check_rows(results)}
          </tbody>
        </table>
      </section>
    </section>

    <script id="trust-report-data" type="application/json">{data_json}</script>
    <script>
      const filters = ["statusFilter", "categoryFilter", "severityFilter", "searchFilter"];
      function elementValue(id) {{
        const element = document.getElementById(id);
        return element ? element.value : "";
      }}
      function attachInputFilter(id, callback) {{
        const element = document.getElementById(id);
        if (element) {{
          element.addEventListener("input", callback);
          element.addEventListener("change", callback);
        }}
      }}
      function applyFilters() {{
        const status = elementValue("statusFilter");
        const category = elementValue("categoryFilter");
        const severity = elementValue("severityFilter");
        const search = elementValue("searchFilter").toLowerCase();
        document.querySelectorAll("#resultsBody tr").forEach((row) => {{
          const visible = (!status || row.dataset.status === status)
            && (!category || row.dataset.category === category)
            && (!severity || row.dataset.severity === severity)
            && (!search || row.textContent.toLowerCase().includes(search));
          row.style.display = visible ? "" : "none";
        }});
      }}
      function applyObjectIssueFilters() {{
        const issueKind = elementValue("objectIssueFilter");
        const search = elementValue("objectSearchFilter").toLowerCase();
        document.querySelectorAll("#objectIssueBody tr").forEach((row) => {{
          const visible = (!issueKind || row.dataset.objectIssueKind === issueKind)
            && (!search || row.textContent.toLowerCase().includes(search));
          row.style.display = visible ? "" : "none";
        }});
      }}
      filters.forEach((id) => attachInputFilter(id, applyFilters));
      ["objectIssueFilter", "objectSearchFilter"].forEach((id) =>
        attachInputFilter(id, applyObjectIssueFilters)
      );
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


def _metric(label: str, value: object, description: str = "") -> str:
    description_html = f"<p>{_h(description)}</p>" if description else ""
    return f"""<div class="panel">
      <div class="metric-label">{_term(label.upper(), label)}</div>
      <div class="metric-value">{value}</div>
      {description_html}
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
          <div class="metric-value">{score}<span class="metric-suffix">/100</span></div>
          <div class="bar" aria-hidden="true"><span style="width:{score}%"></span></div>
        </div>"""
        for category, score in dimension_scores.items()
    )


def _check_rows(results: list[TestResult]) -> str:
    return "\n".join(_check_row(result) for result in results)


def _check_row(result: TestResult) -> str:
    test_case = result.test_case
    description_parts = [test_case.expected_result]
    if test_case.repair_strategy:
        description_parts.append(f"Repair guidance: {test_case.repair_strategy}")
    description = " ".join(description_parts)
    return f"""<tr>
      <td>
        <strong>{_h(test_case.name)}</strong><br>
        <code>{_h(test_case.test_id)}</code>
      </td>
      <td>{_term(test_case.category.value, test_case.category.value)}</td>
      <td>{_h(test_case.severity.value)}</td>
      <td><span class="status {result.status.value.lower()}">{_h(result.status.value)}</span></td>
      <td>{_h(description)}</td>
    </tr>"""


def _excluded_check_table(excluded_checks: list[ExcludedCheck]) -> str:
    if not excluded_checks:
        return "<p>No checks were excluded by rule configuration for this run.</p>"
    return f"""<table>
      <thead>
        <tr>
          <th>Check</th>
          <th>Category</th>
          <th>Reason excluded</th>
        </tr>
      </thead>
      <tbody>
        {"".join(_excluded_check_row(check) for check in excluded_checks)}
      </tbody>
    </table>"""


def _excluded_check_row(check: ExcludedCheck) -> str:
    return f"""<tr>
      <td>
        <strong>{_h(check.name)}</strong><br>
        <code>{_h(check.check_id)}</code>
      </td>
      <td>{_h(check.category)}</td>
      <td>{_h(check.reason)}</td>
    </tr>"""


def _repair_panel(title: str, count: int, text: str) -> str:
    return f"""<div class="panel">
      <div class="metric-label">{_term("REPAIRS", title)}</div>
      <div class="metric-value">{count}</div>
      <p>{_h(text)}</p>
    </div>"""


def _repair_candidate_table(candidates: list[RepairCandidate]) -> str:
    if not candidates:
        return "<p>No repair candidates were generated for this run.</p>"
    return f"""<table>
      <thead>
        <tr>
          <th>Candidate</th>
          <th>Issue</th>
          <th>Mode</th>
          <th>Approval</th>
          <th>Source</th>
          <th>Summary</th>
        </tr>
      </thead>
      <tbody>
        {"".join(_repair_candidate_row(candidate) for candidate in candidates)}
      </tbody>
    </table>"""


def _repair_candidate_row(candidate: RepairCandidate) -> str:
    approval = "Approval required" if candidate.requires_approval else "Safe-auto"
    sql_html = f"<pre>{_h(candidate.sql)}</pre>" if candidate.sql else "<p>No SQL generated.</p>"
    if candidate.sql or candidate.evidence:
        evidence_json = _h(json.dumps(candidate.evidence, indent=2, sort_keys=True, default=str))
    else:
        evidence_json = "{}"
    details_html = f"""<details>
      <summary>Repair details</summary>
      {sql_html}
      <pre>{evidence_json}</pre>
    </details>"""
    return f"""<tr>
      <td><code>{_h(candidate.candidate_id or "REPAIR-CANDIDATE")}</code></td>
      <td>{_h(candidate.issue_code)}</td>
      <td>{_h(candidate.mode.value)}</td>
      <td>{_h(approval)}</td>
      <td><code>{_h(candidate.test_id or "n/a")}</code></td>
      <td>{_h(candidate.summary)}{details_html}</td>
    </tr>"""


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


def _object_issue_section(groups: list[dict[str, object]]) -> str:
    if not groups:
        return """<section class="object-issues" aria-label="Object repair list">
          <h3>Object repair list</h3>
          <p>No missing objects or missing object columns were detected in this run.</p>
        </section>"""
    rows = "\n".join(_object_issue_row(group) for group in groups)
    return f"""<section class="object-issues" aria-label="Object repair list">
      <h3>Object repair list</h3>
      <p>
        Missing objects and objects with missing columns are grouped here so deployment and
        metadata repairs can be planned without opening structured evidence row by row.
      </p>
      <div class="object-issue-toolbar">
        <select id="objectIssueFilter" aria-label="Filter object repair list by issue type">
          <option value="">All object issues</option>
          <option value="MISSING_OBJECT">Missing objects/views</option>
          <option value="MISSING_COLUMN">Objects with missing columns</option>
        </select>
        <input
          id="objectSearchFilter"
          type="search"
          placeholder="Search object, column, check"
          aria-label="Search object repair list"
        />
      </div>
      <table>
        <thead>
          <tr>
            <th>Issue</th>
            <th>Object</th>
            <th>Missing columns</th>
            <th>Checks</th>
            <th>Next step</th>
          </tr>
        </thead>
        <tbody id="objectIssueBody">{rows}</tbody>
      </table>
    </section>"""


def _object_issue_row(group: dict[str, object]) -> str:
    checks = group.get("checks")
    check_items = ""
    if isinstance(checks, list):
        check_items = "<br>".join(_h(str(check)) for check in checks[:5])
    columns = group.get("missing_columns")
    if isinstance(columns, list) and columns:
        column_items = "<br>".join(f"<code>{_h(column)}</code>" for column in columns[:8])
    else:
        column_items = "n/a"
    issue_kind = str(group.get("issue_kind", "UNKNOWN"))
    issue_label = _object_issue_label(issue_kind)
    return f"""<tr
      data-object-issue-kind="{_h(issue_kind)}"
      data-object-name="{_h(group.get("object_name", ""))}"
    >
      <td><span class="status failed">{_h(issue_label)}</span></td>
      <td><code>{_h(group.get("object_name", ""))}</code></td>
      <td>{column_items}</td>
      <td>{check_items}</td>
      <td>{_h(group.get("next_step", "Review the validation evidence."))}</td>
    </tr>"""


def _object_issue_label(issue_kind: str) -> str:
    labels = {
        "MISSING_OBJECT": "Missing object/view",
        "MISSING_COLUMN": "Missing column",
    }
    return labels.get(issue_kind, issue_kind)


def _attention_object_section(groups: list[dict[str, object]]) -> str:
    if not groups:
        return """<section class="attention-objects" aria-label="Objects needing attention">
          <h3>Objects needing attention</h3>
          <p>No object-level failures were detected in this run.</p>
        </section>"""
    rows = "\n".join(_attention_object_row(group) for group in groups)
    return f"""<section class="attention-objects" aria-label="Objects needing attention">
      <h3>Objects needing attention</h3>
      <p>
        Object names are pulled out of failed evidence so missing views and affected tables are
        visible without opening each structured-evidence block.
      </p>
      <table>
        <thead>
          <tr>
            <th>Object</th>
            <th>Issue</th>
            <th>Checks</th>
            <th>Next step</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </section>"""


def _attention_object_row(group: dict[str, object]) -> str:
    checks = group.get("checks")
    check_items = ""
    if isinstance(checks, list):
        check_items = "<br>".join(_h(str(check)) for check in checks[:4])
    return f"""<tr>
      <td><code>{_h(group.get("object_name", ""))}</code></td>
      <td>{_h(group.get("issue_code", "UNKNOWN"))}</td>
      <td>{check_items}</td>
      <td>{_h(group.get("next_step", "Review the validation evidence."))}</td>
    </tr>"""


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
    reference_context = _reference_context(result)
    objects_to_examine = _objects_to_examine(result)
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
    reference_html = ""
    if reference_context:
        reference_html = f"""<div class="reference-context">
          <strong>Referenced from</strong><br>{_h(reference_context)}
        </div>"""
    inspection_html = ""
    if objects_to_examine:
        object_items = "".join(f"<li>{_h(object_name)}</li>" for object_name in objects_to_examine)
        inspection_html = f"""<div class="inspection-scope">
          <strong>Objects to examine</strong>
          <ul>{object_items}</ul>
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
        {reference_html}
        {inspection_html}
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


def _reference_context(result: TestResult) -> str:
    if not result.sample_rows:
        return result.test_case.test_id + ": " + result.test_case.name
    contexts: list[str] = []
    for sample_row in result.sample_rows[:5]:
        label = _sample_reference_label(result, sample_row)
        if label and label not in contexts:
            contexts.append(label)
    return "; ".join(contexts)


def _sample_reference_label(result: TestResult, sample_row: dict[str, object]) -> str:
    recipe_id = sample_row.get("recipe_id")
    recipe_title = sample_row.get("recipe_title")
    if recipe_id and recipe_title:
        return f"Query recipe {recipe_id}: {recipe_title}"
    if recipe_id:
        return f"Query recipe {recipe_id}"
    referenced_from = sample_row.get("referenced_from")
    if referenced_from:
        return str(referenced_from)
    relationship_name = sample_row.get("relationship_name")
    if relationship_name:
        return f"Relationship {relationship_name}"
    scanner = sample_row.get("scanner")
    if scanner:
        return f"Scanner {scanner}: {result.test_case.name}"
    object_names = _dependent_objects_from_sample(sample_row)
    if object_names:
        return ", ".join(object_names)
    return result.test_case.test_id + ": " + result.test_case.name


def _objects_to_examine(result: TestResult) -> list[str]:
    objects: list[str] = []
    for sample_row in result.sample_rows[:5]:
        objects.extend(_sample_objects_to_examine(sample_row))
    return list(dict.fromkeys(objects))


def _sample_objects_to_examine(sample_row: dict[str, object]) -> list[str]:
    objects: list[str] = []
    for field_name in (
        "objects_to_examine",
        "referenced_objects",
        "missing_object",
        "source_object",
        "target_object",
    ):
        field_value = sample_row.get(field_name)
        if isinstance(field_value, str):
            objects.extend(_split_object_list(field_value))
        elif isinstance(field_value, list):
            objects.extend(str(value) for value in field_value if value)
    database_table_pairs = (
        ("database_name", "table_name"),
        ("database_name", "object_name"),
        ("observability_database", "object_name"),
        ("source_database", "source_table"),
        ("target_database", "target_table"),
    )
    for database_field, table_field in database_table_pairs:
        database_name = sample_row.get(database_field)
        table_name = sample_row.get(table_field)
        if database_name and table_name:
            objects.append(f"{database_name}.{table_name}")
    return list(dict.fromkeys(objects))


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
    if issue_code == "NESTED_ORDERED_ANALYTIC":
        return (
            "Rewrite the recipe with staged CTEs: aggregate first, calculate percentage metrics "
            "in the next CTE, then calculate the ordered cumulative percentage in the final SELECT."
        )
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


def _object_issue_groups(results: list[TestResult]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for result in results:
        if result.status == TestStatus.PASSED:
            continue
        for sample_row in result.sample_rows:
            issue_code = str(sample_row.get("issue_code") or "")
            if issue_code == "MISSING_COLUMN":
                missing_column = str(sample_row.get("missing_column") or "")
                object_name = _object_from_missing_column(missing_column)
                column_name = _column_from_missing_column(missing_column)
                if object_name:
                    _add_object_issue(
                        groups,
                        "MISSING_COLUMN",
                        object_name,
                        result,
                        sample_row,
                        missing_column=column_name,
                    )
                continue
            if _is_missing_object_issue(issue_code):
                for object_name in _missing_object_names(sample_row):
                    _add_object_issue(
                        groups,
                        "MISSING_OBJECT",
                        object_name,
                        result,
                        sample_row,
                    )
    return sorted(
        groups.values(),
        key=lambda group: (
            0 if group["issue_kind"] == "MISSING_OBJECT" else 1,
            str(group["object_name"]).lower(),
        ),
    )


def _add_object_issue(
    groups: dict[tuple[str, str], dict[str, object]],
    issue_kind: str,
    object_name: str,
    result: TestResult,
    sample_row: dict[str, object],
    missing_column: str = "",
) -> None:
    key = (issue_kind, object_name)
    group = groups.setdefault(
        key,
        {
            "issue_kind": issue_kind,
            "object_name": object_name,
            "missing_columns": [],
            "checks": [],
            "next_step": _object_issue_next_step(issue_kind, object_name),
        },
    )
    if missing_column and missing_column not in group["missing_columns"]:
        group["missing_columns"].append(missing_column)
    check_label = _result_impact_label(result, sample_row)
    if check_label not in group["checks"]:
        group["checks"].append(check_label)


def _is_missing_object_issue(issue_code: str) -> bool:
    if issue_code == "MISSING_OBJECT":
        return True
    if "COLUMN" in issue_code:
        return False
    return (
        (
            "MISSING" in issue_code
            and any(token in issue_code for token in ("OBJECT", "VIEW", "TABLE"))
        )
        or issue_code.endswith("_NOT_DEPLOYED")
    )


def _missing_object_names(sample_row: dict[str, object]) -> list[str]:
    missing_object = str(sample_row.get("missing_object") or "")
    if missing_object:
        return [missing_object]
    return _attention_object_names(sample_row)


def _object_from_missing_column(missing_column: str) -> str:
    parts = [part for part in missing_column.split(".") if part]
    if len(parts) >= 3:
        return ".".join(parts[-3:-1])
    return ""


def _column_from_missing_column(missing_column: str) -> str:
    parts = [part for part in missing_column.split(".") if part]
    if parts:
        return parts[-1]
    return missing_column


def _object_issue_next_step(issue_kind: str, object_name: str) -> str:
    if issue_kind == "MISSING_OBJECT":
        return (
            f"Deploy {object_name}, create the governed BUS_V/STD_V view, or update metadata "
            "to the deployed name."
        )
    return (
        f"Add the missing column to {object_name}, refresh the view contract, or update stale "
        "recipe metadata."
    )


def _attention_objects(
    results: list[TestResult],
    dependency_index: dict[str, list[str]],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for result in results:
        if result.status == TestStatus.PASSED:
            continue
        for sample_row in result.sample_rows:
            issue_code = str(sample_row.get("issue_code") or result.status.value)
            next_step = _sample_next_step(result, sample_row, issue_code, dependency_index)
            for object_name in _attention_object_names(sample_row):
                key = (object_name, issue_code)
                group = groups.setdefault(
                    key,
                    {
                        "object_name": object_name,
                        "issue_code": issue_code,
                        "checks": [],
                        "next_step": next_step,
                    },
                )
                check_label = _result_impact_label(result, sample_row)
                if check_label not in group["checks"]:
                    group["checks"].append(check_label)
    return sorted(
        groups.values(),
        key=lambda group: (
            _object_attention_rank(str(group["issue_code"]), str(group["object_name"])),
            str(group["object_name"]).lower(),
            str(group["issue_code"]),
        ),
    )


def _attention_object_names(sample_row: dict[str, object]) -> list[str]:
    objects = _sample_objects_to_examine(sample_row)
    missing_column = str(sample_row.get("missing_column") or "")
    if missing_column:
        parts = missing_column.split(".")
        if len(parts) >= 3:
            objects.append(".".join(parts[-3:-1]))
    return list(dict.fromkeys(objects))


def _sample_next_step(
    result: TestResult,
    sample_row: dict[str, object],
    issue_code: str,
    dependency_index: dict[str, list[str]],
) -> str:
    if issue_code == "MISSING_OBJECT":
        object_name = str(sample_row.get("missing_object") or "this object")
        return f"Confirm whether {object_name} should exist as a BUS_V/STD_V view or be renamed."
    if issue_code == "MISSING_COLUMN":
        missing_column = str(sample_row.get("missing_column") or "")
        return _missing_column_alter_hint(missing_column)
    if issue_code in {
        "RELATIONSHIP_SOURCE_NOT_BUS_V",
        "RELATIONSHIP_TARGET_NOT_BUS_V",
        "LINEAGE_SOURCE_NOT_BUS_V",
        "LINEAGE_TARGET_NOT_BUS_V",
    }:
        return "Update metadata to point at the governed BUS_V access view."
    if "MISSING" in issue_code and "VIEW" in issue_code:
        return "Create the missing governed view or update metadata to the deployed view name."
    if issue_code == "NESTED_ORDERED_ANALYTIC":
        return "Rewrite the recipe so each analytic calculation happens in a separate CTE."
    if issue_code.startswith("SQL_"):
        return "Inspect the recipe SQL and replace stale object references or invalid syntax."
    return _next_step(result, dependency_index) or "Review this object's validation evidence."


def _object_attention_rank(issue_code: str, object_name: str) -> tuple[int, int]:
    missing_rank = 0 if "MISSING" in issue_code else 1
    view_rank = 0 if "_V." in object_name.upper() or object_name.upper().endswith("_V") else 1
    return missing_rank, view_rank


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
    relationship_name = sample_row.get("relationship_name")
    if relationship_name:
        return str(relationship_name)
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
    return f"data:image/jpeg;base64,{encoded}"


def _logo_image_data_uri() -> str:
    image = resources.files(_HEADER_IMAGE_PACKAGE).joinpath(_LOGO_IMAGE_NAME).read_bytes()
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _term(term_key: str, label: str) -> str:
    definition = _TERM_DEFINITIONS.get(term_key)
    if not definition:
        return _h(label)
    return f"""<span class="term" title="{_h(definition)}">{_h(label)}</span>"""


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)
