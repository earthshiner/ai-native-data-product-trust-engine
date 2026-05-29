"""Command line entry point for the Trust Engine."""

from __future__ import annotations

import argparse
from pathlib import Path

from ai_native_data_product_trust_engine.adapters import adapter_from_environment
from ai_native_data_product_trust_engine.capabilities import capability_test_cases
from ai_native_data_product_trust_engine.query_templates import query_template_test_cases
from ai_native_data_product_trust_engine.reports import write_json_report
from ai_native_data_product_trust_engine.repairs import (
    apply_safe_repairs,
    generate_repair_candidates,
    write_repair_reports,
)
from ai_native_data_product_trust_engine.test_generation import generate_metadata_tests
from ai_native_data_product_trust_engine.text_references import text_reference_test_cases
from ai_native_data_product_trust_engine.validators import run_validation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adp-trust",
        description="Validate and self-heal AI-native Data Product metadata.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("discover", "generate-tests", "validate", "report"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--prefix", required=True, help="Data Product prefix, e.g. CallCentre")
        if command == "validate":
            subparser.add_argument(
                "--database-url",
                help="SQLAlchemy database URL. Defaults to DATABASE_URI.",
            )
            subparser.add_argument(
                "--output",
                type=Path,
                default=Path("trust-report.json"),
                help="Path for JSON validation evidence.",
            )
            subparser.add_argument(
                "--repair-mode",
                default="proposal",
                choices=("detect", "proposal", "safe-auto"),
                help="Repair posture for validation failures.",
            )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "generate-tests":
        tests = [
            *generate_metadata_tests(args.prefix),
            *capability_test_cases(args.prefix),
            *query_template_test_cases(args.prefix),
            *text_reference_test_cases(args.prefix),
        ]
        for test in tests:
            print(f"{test.test_id}\t{test.category.value}\t{test.name}")
        return 0

    if args.command == "validate":
        tests = generate_metadata_tests(args.prefix)
        adapter = adapter_from_environment(args.database_url)
        run = run_validation(args.prefix, adapter, tests)
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
        print(
            f"Validation complete: {run.passed_count} passed, "
            f"{run.failed_count} failed, {run.error_count} errors. "
            f"Report: {args.output}"
        )
        return 0 if run.failed_count == 0 and run.error_count == 0 else 1

    print(
        f"[ADPTrust.NotImplemented] {args.command} is scaffolded but not implemented yet. "
        "Suggested action: run generate-tests for the first working slice."
    )
    return 2


def _summarise_error(error_message: str) -> str:
    return error_message.split("\n at ", maxsplit=1)[0].strip()


if __name__ == "__main__":
    raise SystemExit(main())
