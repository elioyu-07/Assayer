"""Static conformance CLI for independently packaged Assayer plugins."""

from __future__ import annotations

import argparse
import json

from .conformance import inspect_plugin_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an Assayer plugin package without importing plugin code",
    )
    parser.add_argument(
        "package", nargs="+", metavar="PACKAGE_ROOT",
        help="Directory containing assayer-plugin-release.json",
    )
    args = parser.parse_args(argv)
    reports = [inspect_plugin_package(path) for path in args.package]
    payload = {
        "schemaVersion": "1.0.0",
        "status": "passed" if all(report.passed for report in reports) else "failed",
        "plugins": [report.as_dict() for report in reports],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
