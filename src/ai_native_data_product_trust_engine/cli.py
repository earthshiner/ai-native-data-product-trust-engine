"""Command line entry point for the Trust Engine."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ai_native_data_product_trust_engine.adapters import (
    DatabaseUnavailableError,
    LoggingAdapter,
    adapter_from_environment,
    configure_logging,
    database_uri_hint,
)
from ai_native_data_product_trust_engine.capabilities import capability_test_cases
from ai_native_data_product_trust_engine.html_reports import write_html_report
from ai_native_data_product_trust_engine.query_templates import query_template_test_cases
from ai_native_data_product_trust_engine.relationship_health import (
    relationship_health_test_cases,
)
from ai_native_data_product_trust_engine.repairs import (
    apply_safe_repairs,
    generate_repair_candidates,
    write_repair_reports,
)
from ai_native_data_product_trust_engine.reports import write_json_report
from ai_native_data_product_trust_engine.rule_config import load_rule_config
from ai_native_data_product_trust_engine.test_generation import generate_metadata_tests
from ai_native_data_product_trust_engine.text_references import text_reference_test_cases
from ai_native_data_product_trust_engine.trust_publish import (
    default_trust_table,
    publish_trust_result,
)
from ai_native_data_product_trust_engine.validators import run_validation
from ai_native_data_product_trust_engine.view_contracts import view_contract_test_cases

LOGGER = logging.getLogger("ai_native_data_product_trust_engine.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adp-trust-engine",
        description="Validate and self-heal AI-native Data Product metadata.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("discover", "generate-tests", "validate", "report"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--prefix", required=True, help="Data Product prefix, e.g. ProductPrefix")
        if command in {"generate-tests", "validate"}:
            subparser.add_argument(
                "--rules-config",
                type=Path,
                help="Optional JSON file with disabled_test_ids and disabled_scanners arrays.",
            )
        if command == "validate":
            subparser.add_argument(
                "--database-url",
                help="SQLAlchemy database URL. Defaults to DATABASE_URI.",
            )
            subparser.add_argument(
                "--log-file",
                type=Path,
                help=(
                    "Optional log file for diagnostic detail, including SQL before execution "
                    "and SQL that fails."
                ),
            )
            subparser.add_argument(
                "--log-level",
                choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
                help=(
                    "Logging level. Defaults to INFO when --log-file is supplied, otherwise "
                    "WARNING."
                ),
            )
            subparser.add_argument(
                "--output",
                type=Path,
                default=Path("trust-report.json"),
                help="Path for JSON validation evidence.",
            )
            subparser.add_argument(
                "--html-output",
                type=Path,
                help="Optional path for a standalone interactive HTML report.",
            )
            subparser.add_argument(
                "--repair-mode",
                default="proposal",
                choices=("detect", "proposal", "safe-auto"),
                help="Repair posture for validation failures.",
            )
            subparser.add_argument(
                "--publish-trust-table",
                nargs="?",
                const="",
                help=(
                    "Publish a compact trust summary row for agent reads. Optional value is a "
                    "two-part Teradata table name; falls back to the rules-config "
                    "publish_trust_table, then <prefix>_SEM_STD_T.trust_engine_run."
                ),
            )
            subparser.add_argument(
                "--enable-helpstats",
                action="store_true",
                help=(
                    "Enable Teradata DIAGNOSTIC HELPSTATS for each cookbook EXPLAIN. "
                    "Suggestions are advisory and are reported as performance findings."
                ),
            )

    mcp_parser = subparsers.add_parser(
        "mcp-server",
        help="Serve agent-friendly MCP resources over local Trust Engine reports.",
    )
    mcp_parser.add_argument(
        "--reports-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing Trust Engine JSON reports. "
            "Overrides [mcp].reports_dir in config/mcp.toml (default: reports)."
        ),
    )
    mcp_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        dest="config_path",
        help=(
            "Path to mcp.toml. Overrides the $ADP_TRUST_MCP_CONFIG env var "
            "and the default config/mcp.toml lookup."
        ),
    )
    mcp_parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=None,
        help=(
            "MCP transport to use. Overrides [mcp].transport in config/mcp.toml "
            "(default: stdio). 'streamable-http' for HTTP deployments "
            "(MCP 2025-03-26); 'sse' for legacy SSE clients."
        ),
    )
    mcp_parser.add_argument(
        "--host",
        default=None,
        help=(
            "Host address to bind for HTTP transports (default: 127.0.0.1). "
            "Use 0.0.0.0 only behind a network-layer access control."
        ),
    )
    mcp_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on for HTTP transports (default: 8000).",
    )
    mcp_parser.add_argument(
        "--path",
        dest="http_path",
        default=None,
        help=(
            "URL path for the MCP endpoint "
            "(default: /mcp for streamable-http, /sse for sse)."
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except DatabaseUnavailableError as exc:
        # Fatal: the database is unreachable. Stop with a clear message and a
        # distinct exit code — no partial, all-ERROR report is written.
        print(str(exc), file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI boundary must never leak driver stacks.
        print(_friendly_cli_error(exc), file=sys.stderr)
        return 2


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "generate-tests":
        rule_config = load_rule_config(args.rules_config)
        tests = [*generate_metadata_tests(args.prefix)]
        if "CAPABILITY" not in rule_config.disabled_scanners:
            tests.extend(capability_test_cases(args.prefix))
        if "QUERY" not in rule_config.disabled_scanners:
            tests.extend(query_template_test_cases(args.prefix))
        if "RELATIONSHIP" not in rule_config.disabled_scanners:
            tests.extend(relationship_health_test_cases(args.prefix))
        if "TEXT" not in rule_config.disabled_scanners:
            tests.extend(text_reference_test_cases(args.prefix))
        if "VIEW" not in rule_config.disabled_scanners:
            tests.extend(view_contract_test_cases(args.prefix))
        tests = rule_config.filter_tests(tests)
        for test in tests:
            print(f"{test.test_id}\t{test.category.value}\t{test.name}")
        return 0

    if args.command == "validate":
        configure_logging(args.log_level, args.log_file)
        LOGGER.info("Starting validation for product prefix %s.", args.prefix)
        rule_config = load_rule_config(args.rules_config)
        generated_tests = generate_metadata_tests(args.prefix)
        tests = rule_config.filter_tests(generated_tests)
        excluded_checks = rule_config.excluded_checks(generated_tests)
        LOGGER.info(
            "Prepared %s checks; %s checks excluded by configuration.",
            len(tests),
            len(excluded_checks),
        )
        adapter = LoggingAdapter(adapter_from_environment(args.database_url))
        try:
            run = run_validation(
                args.prefix,
                adapter,
                tests,
                excluded_checks=excluded_checks,
                enable_helpstats=args.enable_helpstats,
                **rule_config.scanner_kwargs(),
            )
            LOGGER.info(
                "Validation completed: %s passed, %s failed, %s errors.",
                run.passed_count,
                run.failed_count,
                run.error_count,
            )
            write_json_report(run, args.output)
            repair_candidates = []
            if args.repair_mode in {"proposal", "safe-auto"}:
                repair_candidates = generate_repair_candidates(run)
                markdown_path, sql_path = write_repair_reports(repair_candidates, args.output)
                print(f"Repair candidates: {len(repair_candidates)}. Reports: {markdown_path}, {sql_path}")
            if args.repair_mode == "safe-auto":
                applications = apply_safe_repairs(adapter, repair_candidates)
                applied_count = sum(1 for application in applications if application.applied)
                failed_count = sum(1 for application in applications if not application.applied)
                print(f"Safe-auto repairs applied: {applied_count}; failed: {failed_count}")
                for application in applications:
                    if application.error_message:
                        print(
                            f"[ADPTrust.RepairApplyFailed] {application.candidate.candidate_id}. "
                            f"{_summarise_error(application.error_message)}"
                        )
            if args.html_output:
                write_html_report(run, args.html_output, repair_candidates)
                print(f"HTML report: {args.html_output}")
            if args.publish_trust_table is not None:
                # Target precedence: explicit CLI value, then the rules-config
                # publish_trust_table, then the engine default. The CLI flag
                # remains the publish trigger either way.
                trust_table = (
                    args.publish_trust_table
                    or rule_config.publish_trust_table
                    or default_trust_table(args.prefix)
                )
                published_table = publish_trust_result(adapter, run, repair_candidates, trust_table)
                print(f"Trust summary published: {published_table}")
            print(
                f"Validation complete: {run.passed_count} passed, "
                f"{run.failed_count} failed, {run.error_count} errors. "
                f"Report: {args.output}"
            )
            return 0 if run.failed_count == 0 and run.error_count == 0 else 1
        finally:
            # Always release the pooled Teradata session — even when validation
            # aborts (e.g. the database is unreachable) — so a failed run never
            # leaves a virtual circuit tied up.
            adapter.close()

    if args.command == "mcp-server":
        from ai_native_data_product_trust_engine.mcp_server import run_mcp_server

        if args.transport == "stdio" and any(
            [args.host, args.port, args.http_path]
        ):
            print(
                "[ADPTrust.InvalidArgs] --host, --port and --path are only valid "
                "with --transport streamable-http or --transport sse.",
                file=sys.stderr,
            )
            return 2

        run_mcp_server(
            args.reports_dir,
            transport=args.transport,
            host=args.host,
            port=args.port,
            http_path=args.http_path,
            config_path=args.config_path,
        )
        return 0

    print(
        f"[ADPTrust.NotImplemented] {args.command} is scaffolded but not implemented yet. "
        "Suggested action: run generate-tests for the first working slice."
    )
    return 2


def _summarise_error(error_message: str) -> str:
    return error_message.split("\n at ", maxsplit=1)[0].strip()


def _friendly_cli_error(exc: Exception) -> str:
    message = _summarise_error(str(exc))
    if message.startswith("[ADPTrust."):
        return message
    lowered = message.lower()
    if "can't load plugin" in lowered and "teradatasql" in lowered:
        # Not a DATABASE_URI problem: the teradatasql SQLAlchemy dialect is not
        # importable in this interpreter (usually a bare `python` without the
        # project's dependencies).
        return (
            "[ADPTrust.MissingDialect] The teradatasql SQLAlchemy dialect is not available "
            "in this interpreter, so the database URL could not be opened. "
            'Suggested action: install the teradata optional dependencies (pip install -e '
            '".[teradata]") or run through .\\adp.ps1, which always uses this project\'s '
            f"virtual environment. {database_uri_hint()}"
        )
    return (
        "[ADPTrust.ValidationFailed] Validation could not complete. "
        f"{message} "
        "Suggested action: check DATABASE_URI, network/VPN access, credentials, and Teradata "
        f"service availability, then rerun validate. {database_uri_hint()}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
