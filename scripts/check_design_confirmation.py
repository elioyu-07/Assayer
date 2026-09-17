#!/usr/bin/env python3
"""Validate a Design Confirmation before implementation is allowed."""

from __future__ import annotations

import json
import fnmatch
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "design-confirmation.schema.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("design confirmation must be a JSON object")
    return value


def validate_confirmation(path: Path, *, root: Path = ROOT) -> tuple[str, ...]:
    try:
        value = _load(path)
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return (str(error),)

    errors = [error.message for error in Draft202012Validator(schema).iter_errors(value)]
    if errors:
        return tuple(sorted(errors))

    issues: list[str] = []
    for relative in value["authorityDocuments"]:
        if not (root / relative).is_file():
            issues.append(f"authority document does not exist: {relative}")

    impact = value["publicContractImpact"]
    approval = value["humanApproval"]
    requires_human = impact != "internal_only"
    if approval["required"] != requires_human:
        issues.append(
            "humanApproval.required must be true for public/semantic/safety/breaking "
            "changes and false only for internal_only changes"
        )
    if approval["status"] == "approved" and not approval.get("approver"):
        issues.append("approved humanApproval requires approver")
    if approval["status"] == "approved" and not approval.get("approvedAt"):
        issues.append("approved humanApproval requires approvedAt")
    if value["state"] in {"approved", "implemented", "verified"} and approval["status"] != "approved":
        issues.append("implementation states require approved humanApproval")
    return tuple(sorted(issues))


def validate_changed_paths(
    records: list[dict[str, Any]], changed_paths: list[str],
) -> tuple[str, ...]:
    approved = [
        record for record in records
        if record.get("state") in {"approved", "implemented", "verified"}
        and record.get("humanApproval", {}).get("status") == "approved"
    ]
    issues: list[str] = []
    for changed in changed_paths:
        if changed.startswith("design/changes/"):
            continue
        owners = [
            record["changeId"] for record in approved
            if any(fnmatch.fnmatchcase(changed, pattern) for pattern in record["changedPaths"])
        ]
        if not owners:
            issues.append(f"changed path is not covered by an approved design: {changed}")
    return tuple(sorted(issues))


def _git_changed_paths(root: Path, git_range: str | None, staged: bool) -> list[str]:
    command = ["git", "diff", "--name-only"]
    if staged:
        command.append("--cached")
    elif git_range:
        command.append(git_range)
    command.append("--")
    result = subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line]


def validate_repository(root: Path = ROOT, *, git_range: str | None = None, staged: bool = False) -> tuple[str, ...]:
    records: list[dict[str, Any]] = []
    issues: list[str] = []
    for path in sorted((root / "design" / "changes").glob("*.json")):
        issues.extend(f"{path.name}: {issue}" for issue in validate_confirmation(path, root=root))
        try:
            records.append(_load(path))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    try:
        changed = _git_changed_paths(root, git_range, staged)
    except (OSError, subprocess.CalledProcessError) as error:
        return tuple(sorted((*issues, f"cannot inspect git changes: {error}")))
    issues.extend(validate_changed_paths(records, changed))
    return tuple(sorted(issues))


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    if "--staged" in arguments or "--git-range" in arguments:
        if "--staged" in arguments and "--git-range" in arguments:
            print("--staged and --git-range are mutually exclusive", file=sys.stderr)
            return 2
        git_range = None
        if "--git-range" in arguments:
            index = arguments.index("--git-range")
            if index + 1 >= len(arguments):
                print("--git-range requires a revision range", file=sys.stderr)
                return 2
            git_range = arguments[index + 1]
        issues = validate_repository(ROOT, git_range=git_range, staged="--staged" in arguments)
        if issues:
            print("Design confirmation repository gate rejected:", file=sys.stderr)
            for issue in issues:
                print(f"- {issue}", file=sys.stderr)
            return 1
        print("Design confirmation repository gate passed.")
        return 0
    if len(arguments) != 1:
        print("usage: check_design_confirmation.py PATH | --staged | --git-range RANGE", file=sys.stderr)
        return 2
    path = Path(arguments[0]).expanduser().resolve()
    issues = validate_confirmation(path)
    if issues:
        print(f"Design confirmation rejected: {path}", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print(f"Design confirmation passed: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
