"""Agent-facing MCP orientation layer over Trust Engine report evidence."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ai_native_data_product_trust_engine.repairs import _candidate_from_sample

try:  # pragma: no cover - exercised only when the optional MCP extra is installed.
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - normal test environments need no MCP dependency.
    FastMCP = None

TRUST_RESOURCE_BASE = "trust://products"
ORIENTATION_NAVIGATION = (
    "orientation",
    "latest-report",
    "scores",
    "checks",
    "failures",
    "repair-candidates",
)


def create_mcp_server(reports_dir: str | Path = Path("reports")):
    """Create a FastMCP server for Trust Engine report discovery."""
    if FastMCP is None:
        raise RuntimeError(
            "[ADPTrust.MCPUnavailable] MCP SDK is not installed. "
            "Suggested action: install the optional MCP extra with "
            "`pip install .[mcp]`, then rerun `adp-trust mcp-server`."
        )

    report_root = Path(reports_dir)
    server = FastMCP("AI-Native Data Product Trust Engine")

    @server.resource(f"{TRUST_RESOURCE_BASE}")
    def list_products() -> str:
        return _json(discover_products_payload(report_root))

    @server.resource(f"{TRUST_RESOURCE_BASE}/{{prefix}}/orientation")
    def product_orientation(prefix: str) -> str:
        return _json(build_orientation_resource(load_latest_report(prefix, report_root)))

    @server.resource(f"{TRUST_RESOURCE_BASE}/{{prefix}}/latest-report")
    def latest_report(prefix: str) -> str:
        return _json(load_latest_report(prefix, report_root))

    @server.resource(f"{TRUST_RESOURCE_BASE}/{{prefix}}/scores")
    def product_scores(prefix: str) -> str:
        return _json(build_scores_resource(load_latest_report(prefix, report_root)))

    @server.resource(f"{TRUST_RESOURCE_BASE}/{{prefix}}/checks")
    def product_checks(prefix: str) -> str:
        return _json(build_checks_resource(load_latest_report(prefix, report_root)))

    @server.resource(f"{TRUST_RESOURCE_BASE}/{{prefix}}/failures")
    def product_failures(prefix: str) -> str:
        return _json(build_failures_resource(load_latest_report(prefix, report_root)))

    @server.resource(f"{TRUST_RESOURCE_BASE}/{{prefix}}/repair-candidates")
    def product_repairs(prefix: str) -> str:
        return _json(build_repair_candidates_resource(load_latest_report(prefix, report_root)))

    @server.tool()
    def search_data_products() -> dict[str, Any]:
        """List validated data products with their first orientation resource."""
        return discover_products_payload(report_root)

    @server.tool()
    def describe_data_product(prefix: str) -> dict[str, Any]:
        """Return the metadata-first orientation manifest for a data product."""
        return build_orientation_resource(load_latest_report(prefix, report_root))

    @server.tool()
    def get_recommended_entrypoint(prefix: str) -> dict[str, str]:
        """Return the first resource an agent should read for a product."""
        report = load_latest_report(prefix, report_root)
        return {
            "prefix": str(report.get("prefix") or prefix),
            "recommended_entrypoint": _resource_uri(
                str(report.get("prefix") or prefix),
                "orientation",
            ),
        }

    @server.tool()
    def list_failed_checks(prefix: str) -> dict[str, Any]:
        """List failed and errored checks from the latest report."""
        return build_failures_resource(load_latest_report(prefix, report_root))

    @server.tool()
    def generate_repair_plan(prefix: str) -> dict[str, Any]:
        """Generate approval-aware repair candidates from the latest report."""
        return build_repair_candidates_resource(load_latest_report(prefix, report_root))

    @server.tool()
    def explain_check(prefix: str, test_id: str) -> dict[str, Any]:
        """Explain one check and its latest result."""
        return build_check_explanation(load_latest_report(prefix, report_root), test_id)

    @server.prompt()
    def trust_orientation_prompt(prefix: str) -> str:
        return (
            f"Start with {_resource_uri(prefix, 'orientation')}. "
            "Read the contract and scores first, inspect failures and repair candidates next, "
            "and only use the data product after the approved trust path is clear."
        )

    return server


def run_mcp_server(reports_dir: str | Path = Path("reports")) -> None:
    """Run the Trust Engine MCP server over local report artifacts."""
    create_mcp_server(reports_dir).run()


def discover_products_payload(reports_dir: str | Path) -> dict[str, Any]:
    products = []
    for prefix, report_path in _latest_report_paths(Path(reports_dir)).items():
        report = _load_json(report_path)
        products.append(
            {
                "prefix": prefix,
                "orientation": _resource_uri(prefix, "orientation"),
                "latest_report": _resource_uri(prefix, "latest-report"),
                "completed_at": report.get("completed_at"),
                "summary": report.get("summary", {}),
                "scores": report.get("scores", {}),
            }
        )
    products.sort(key=lambda product: product["prefix"].lower())
    return {
        "resource": TRUST_RESOURCE_BASE,
        "purpose": "List data products with trust evidence before agents inspect details.",
        "products": products,
    }


def load_latest_report(prefix: str, reports_dir: str | Path) -> dict[str, Any]:
    report_paths = _latest_report_paths(Path(reports_dir))
    for candidate_prefix, report_path in report_paths.items():
        if candidate_prefix.lower() == prefix.lower():
            return _load_json(report_path)
    raise ValueError(
        f"[ADPTrust.ReportNotFound] No Trust Engine report found for prefix {prefix}. "
        "Suggested action: run `adp-trust validate --prefix "
        f"{prefix} --output reports/{prefix.lower()}-validation.json` first."
    )


def build_orientation_resource(report: dict[str, Any]) -> dict[str, Any]:
    prefix = str(report.get("prefix") or "unknown")
    failures = _failure_results(report)
    critical_failures = [
        result
        for result in failures
        if result.get("test_case", {}).get("severity") == "CRITICAL"
    ]
    repair_candidates = build_repair_candidates_resource(report)["repair_candidates"]
    return {
        "resource": _resource_uri(prefix, "orientation"),
        "data_product_id": prefix,
        "name": f"{prefix} Data Product",
        "version": _metadata_value(report, "product_version", "unknown"),
        "purpose": (
            "Data Product Orientation Layer: expose trust evidence before an agent "
            "touches data or product-specific SQL."
        ),
        "entrypoints": {
            section: _resource_uri(prefix, section)
            for section in ORIENTATION_NAVIGATION
        },
        "recommended_navigation": list(ORIENTATION_NAVIGATION[1:]),
        "metadata_first_handshake": [
            "list data products",
            "read orientation manifest",
            "inspect scores, checks, failures and repair candidates",
            "resolve blocking trust issues",
            "only then use approved data access outside the Trust Engine",
        ],
        "safe_tools": [
            "search_data_products",
            "describe_data_product",
            "get_recommended_entrypoint",
            "list_failed_checks",
            "generate_repair_plan",
            "explain_check",
        ],
        "approval_required_tools": [],
        "latest_run": {
            "started_at": report.get("started_at"),
            "completed_at": report.get("completed_at"),
            "summary": report.get("summary", {}),
        },
        "scores": report.get("scores", {}),
        "status": {
            "safe_for_broad_agent_use": not critical_failures,
            "failure_count": len(failures),
            "critical_failure_count": len(critical_failures),
            "repair_candidate_count": len(repair_candidates),
            "recommended_next_resource": _resource_uri(
                prefix, "failures" if failures else "latest-report"
            ),
        },
    }


def build_scores_resource(report: dict[str, Any]) -> dict[str, Any]:
    prefix = str(report.get("prefix") or "unknown")
    return {
        "resource": _resource_uri(prefix, "scores"),
        "prefix": prefix,
        "scores": report.get("scores", {}),
        "dimension_scores": report.get("dimension_scores", {}),
        "summary": report.get("summary", {}),
    }


def build_checks_resource(report: dict[str, Any]) -> dict[str, Any]:
    prefix = str(report.get("prefix") or "unknown")
    checks = []
    for result in report.get("results", []):
        test_case = result.get("test_case", {})
        checks.append(
            {
                "test_id": test_case.get("test_id"),
                "name": test_case.get("name"),
                "category": test_case.get("category"),
                "severity": test_case.get("severity"),
                "status": result.get("status"),
                "expected": test_case.get("expected"),
                "expected_result": test_case.get("expected_result"),
                "repair_strategy": test_case.get("repair_strategy"),
            }
        )
    return {
        "resource": _resource_uri(prefix, "checks"),
        "prefix": prefix,
        "check_count": len(checks),
        "checks": checks,
    }


def build_failures_resource(report: dict[str, Any]) -> dict[str, Any]:
    prefix = str(report.get("prefix") or "unknown")
    failures = []
    for result in _failure_results(report):
        test_case = result.get("test_case", {})
        sample_rows = result.get("sample_rows") or []
        failures.append(
            {
                "test_id": test_case.get("test_id"),
                "name": test_case.get("name"),
                "category": test_case.get("category"),
                "severity": test_case.get("severity"),
                "status": result.get("status"),
                "row_count": result.get("row_count"),
                "consequence": _failure_consequence(test_case.get("test_id"), sample_rows),
                "evidence_summary": _evidence_summary(sample_rows, result.get("error_message")),
                "repair_strategy": test_case.get("repair_strategy"),
            }
        )
    return {
        "resource": _resource_uri(prefix, "failures"),
        "prefix": prefix,
        "failure_count": len(failures),
        "failures": failures,
    }


def build_repair_candidates_resource(report: dict[str, Any]) -> dict[str, Any]:
    prefix = str(report.get("prefix") or "unknown")
    candidates = []
    for result in _failure_results(report):
        test_case = result.get("test_case", {})
        test_id = str(test_case.get("test_id") or "UNKNOWN-CHECK")
        for sample_row in result.get("sample_rows") or [{}]:
            candidate = _candidate_from_sample(test_id, sample_row)
            if candidate:
                candidates.append(asdict(candidate) | {"mode": candidate.mode.value})
    return {
        "resource": _resource_uri(prefix, "repair-candidates"),
        "prefix": prefix,
        "safe_auto_count": sum(1 for candidate in candidates if not candidate["requires_approval"]),
        "approval_required_count": sum(
            1 for candidate in candidates if candidate["requires_approval"]
        ),
        "repair_candidates": candidates,
    }


def build_check_explanation(report: dict[str, Any], test_id: str) -> dict[str, Any]:
    prefix = str(report.get("prefix") or "unknown")
    for result in report.get("results", []):
        test_case = result.get("test_case", {})
        if str(test_case.get("test_id")).lower() == test_id.lower():
            return {
                "resource": _resource_uri(prefix, "checks"),
                "prefix": prefix,
                "test_id": test_case.get("test_id"),
                "name": test_case.get("name"),
                "category": test_case.get("category"),
                "severity": test_case.get("severity"),
                "status": result.get("status"),
                "expected_result": test_case.get("expected_result"),
                "repair_strategy": test_case.get("repair_strategy"),
                "consequence": _failure_consequence(
                    test_case.get("test_id"),
                    result.get("sample_rows"),
                ),
                "sample_rows": result.get("sample_rows", []),
            }
    raise ValueError(
        f"[ADPTrust.CheckNotFound] Check {test_id} was not found in the latest {prefix} report. "
        "Suggested action: list the checks resource and retry with a valid test_id."
    )


def _latest_report_paths(reports_dir: Path) -> dict[str, Path]:
    latest: dict[str, tuple[str, Path]] = {}
    if not reports_dir.exists():
        return {}
    for report_path in reports_dir.glob("*.json"):
        if report_path.name.endswith(".repairs.json"):
            continue
        try:
            report = _load_json(report_path)
        except (OSError, json.JSONDecodeError):
            continue
        prefix = report.get("prefix")
        if not isinstance(prefix, str) or not prefix:
            continue
        completed_at = str(report.get("completed_at") or "")
        if prefix not in latest or completed_at > latest[prefix][0]:
            latest[prefix] = (completed_at, report_path)
    return {prefix: report_path for prefix, (_, report_path) in latest.items()}


def _load_json(report_path: Path) -> dict[str, Any]:
    return json.loads(report_path.read_text(encoding="utf-8"))


def _resource_uri(prefix: str, section: str) -> str:
    return f"{TRUST_RESOURCE_BASE}/{prefix}/{section}"


def _failure_results(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        result
        for result in report.get("results", [])
        if result.get("status") in {"FAILED", "ERROR"}
    ]


def _evidence_summary(sample_rows: object, error_message: object) -> str:
    if error_message:
        return str(error_message)
    if not isinstance(sample_rows, list) or not sample_rows:
        return "No sample evidence was returned."
    first_row = sample_rows[0]
    if not isinstance(first_row, dict):
        return "Sample evidence is available in the full report."
    issue_code = first_row.get("issue_code")
    repair_hint = first_row.get("repair_hint")
    if issue_code and repair_hint:
        return f"{issue_code}: {repair_hint}"
    if issue_code:
        return str(issue_code)
    return "Sample evidence is available in the full report."


def _failure_consequence(test_id: object, sample_rows: object) -> str:
    issue_codes = {
        str(row.get("issue_code"))
        for row in sample_rows or []
        if isinstance(row, dict) and row.get("issue_code")
    }
    if "MISSING_STANDARD_LOCKING_VIEW" in issue_codes:
        return (
            "Agents or applications may query product tables directly, potentially blocking "
            "other queries that need the same table."
        )
    if str(test_id).endswith("DISCOVERY-001"):
        return "Agents cannot reliably discover the product-first orientation manifest."
    if str(test_id).endswith("DISCOVERY-002"):
        return "Agents may follow stale or incomplete product metadata before accessing data."
    return "Agent trust, repair planning or approved data access may be degraded until fixed."


def _metadata_value(report: dict[str, Any], key: str, default: str) -> str:
    for result in report.get("results", []):
        for sample_row in result.get("sample_rows") or []:
            if isinstance(sample_row, dict) and sample_row.get(key):
                return str(sample_row[key])
    return default


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)
