"""Relocate the Spec-quality plugin into a standalone distribution (one-time).

This helper copies the Spec-quality runtime, review, and evaluation modules and
their policy resources out of the platform source into
``plugins/spec-quality/src/assayer_spec_quality/``, rewriting the platform
imports from relative (``from ...contract``) to absolute
(``from assayer_platform.contract``). Intra-package imports (``from .review``)
are preserved.

The result is committed source; this script is a migration aid, not a runtime
dependency. Re-running it regenerates the module bodies from the current
built-in implementation and overwrites any local edits.
"""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILTIN = ROOT / "src" / "assayer_platform" / "builtin_plugins" / "spec_quality"
TARGET = ROOT / "plugins" / "spec-quality" / "src" / "assayer_spec_quality"

IMPORT_REWRITES = (
    ("from ...source_fact_index import", "from assayer_platform.source_fact_index import"),
    ("from ...source_chunking import", "from assayer_platform.source_chunking import"),
    ("from ...review_protocol import", "from assayer_platform.review_protocol import"),
    ("from ...actionable_result import", "from assayer_platform.actionable_result import"),
    ("from ...evidence_graph import", "from assayer_platform.evidence_graph import"),
    ("from ...navigation import", "from assayer_platform.navigation import"),
    ("from ...evaluation import", "from assayer_platform.evaluation import"),
    ("from ...registry import", "from assayer_platform.registry import"),
    ("from ...identity import", "from assayer_platform.identity import"),
    ("from ...contract import", "from assayer_platform.contract import"),
    ("from ... import ", "from assayer_platform import "),
)

# Files copied verbatim as data resources (kept beside runtime.py because the
# runtime loads manifest/policy/checklist relative to __file__).
DATA_SUFFIXES = (
    ".json", ".md",
)

MODULES = ("runtime.py", "review.py", "evaluation.py")


def _transform(text: str) -> str:
    for old, new in IMPORT_REWRITES:
        text = text.replace(old, new)
    return text


def build() -> None:
    if not BUILTIN.is_dir():
        raise SystemExit(f"built-in source missing: {BUILTIN}")
    TARGET.mkdir(parents=True, exist_ok=True)
    for module in MODULES:
        source = BUILTIN / module
        if not source.is_file():
            raise SystemExit(f"module missing: {source}")
        (TARGET / module).write_text(_transform(source.read_text(encoding="utf-8")), encoding="utf-8")
    for path in sorted(BUILTIN.iterdir()):
        if path.is_dir():
            if path.name == "__pycache__":
                continue
            shutil.copytree(path, TARGET / path.name, dirs_exist_ok=True)
            continue
        if path.name in MODULES or path.name == "__init__.py":
            continue
        if not any(path.name.endswith(suffix) for suffix in DATA_SUFFIXES):
            continue
        shutil.copy2(path, TARGET / path.name)


if __name__ == "__main__":
    build()
    print(f"Wrote {TARGET}")
