"""Command line entry point for the Trust Engine."""

from __future__ import annotations

import argparse

from ai_native_data_product_trust_engine.test_generation import generate_metadata_tests


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
                "--repair-mode",
                default="proposal",
                choices=("detect", "proposal", "safe-auto"),
                help="Repair posture for validation failures.",
            )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "generate-tests":
        tests = generate_metadata_tests(args.prefix)
        for test in tests:
            print(f"{test.test_id}\t{test.category.value}\t{test.name}")
        return 0

    print(
        f"[ADPTrust.NotImplemented] {args.command} is scaffolded but not implemented yet. "
        "Suggested action: run generate-tests for the first working slice."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
