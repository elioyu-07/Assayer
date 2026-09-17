#!/usr/bin/env python3
"""Enforce domain-neutral vocabulary in normative platform documents.

Concrete domain names belong to a plugin, provider, example, or historical
design record.  They must not become platform vocabulary merely because a
vertical implementation was the first consumer.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


NORMATIVE_DOCUMENTS = (
    "docs/plugin-contract-v1.md",
    "docs/plugin-development-standard-v1.md",
    "docs/capability-provider-contract-v1.md",
    "docs/canonical-result-contract-v1.md",
    "docs/plugin-development.md",
    "docs/platform-constitution-v2.md",
    "docs/design-confirmation-contract-v1.md",
)

# These are plugin/domain names, not generic platform concepts.  Keep this
# list deliberately narrow: provider kinds such as browser or file remain
# valid examples in the generic capability contract.
FORBIDDEN_DOMAIN_TERMS = (
    re.compile(r"\bfront(?:end)?\b", re.IGNORECASE),
    re.compile(r"\bfront[- ]end\b", re.IGNORECASE),
    re.compile(r"\bfrontend[A-Z][A-Za-z0-9_]*\b"),
    re.compile(r"\bfua(?:[-_]?\d+)?\b", re.IGNORECASE),
    re.compile(r"\bass[-_]spec\b", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])spec(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"\bSpec[A-Z][A-Za-z0-9_]*\b"),
)

# Retired ordinary-plugin runtime concepts must not re-enter an active
# normative document. Provider Python packaging remains valid, but an ordinary
# plugin is declaration-only and emits one compiled JSON artifact.
FORBIDDEN_RETIRED_PLUGIN_TERMS = (
    re.compile(r"\bAdvanced SPI\b", re.IGNORECASE),
    re.compile(r"\bSimple SDK\b", re.IGNORECASE),
    re.compile(r"\bPolicy Pack\b", re.IGNORECASE),
    re.compile(r"\bPluginRegistration\b", re.IGNORECASE),
    re.compile(r"\bDomainResultContract\b", re.IGNORECASE),
    re.compile(r"\bscan\(document\)\b", re.IGNORECASE),
    re.compile(r"\brelations\(documents\)\b", re.IGNORECASE),
    re.compile(r"\b(?:isolated|exact)[ -]wheel\b", re.IGNORECASE),
    re.compile(r"\bassayer\.plugins\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class DocumentViolation:
    path: Path
    line: int
    term: str
    text: str


def find_violations(root: Path) -> tuple[DocumentViolation, ...]:
    violations: list[DocumentViolation] = []
    for relative in NORMATIVE_DOCUMENTS:
        path = root / relative
        if not path.is_file():
            violations.append(DocumentViolation(path, 0, "missing document", ""))
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in FORBIDDEN_DOMAIN_TERMS:
                match = pattern.search(line)
                if match:
                    violations.append(
                        DocumentViolation(path, line_number, match.group(0), line.strip())
                    )
                    break
            else:
                for pattern in FORBIDDEN_RETIRED_PLUGIN_TERMS:
                    match = pattern.search(line)
                    if match:
                        violations.append(
                            DocumentViolation(path, line_number, match.group(0), line.strip())
                        )
                        break
    return tuple(violations)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = find_violations(root)
    if violations:
        for violation in violations:
            relative = violation.path.relative_to(root)
            print(f"{relative}:{violation.line}: forbidden domain term {violation.term!r}")
            if violation.text:
                print(f"  {violation.text}")
        return 1
    print("Normative document boundary check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
