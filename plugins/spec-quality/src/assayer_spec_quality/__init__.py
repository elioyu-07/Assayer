"""Standalone Spec-quality audit plugin (independent distribution).

This package is the externalized Spec domain. It depends on the Assayer
platform (``assayer``) and exposes one ``assayer.plugins`` entry point,
``registration``, that the platform loads after static package validation.

The module bodies in ``runtime.py``, ``review.py``, and ``evaluation.py`` are
relocated from the platform source and use absolute ``assayer_platform``
imports; see ``scripts/build_spec_quality_plugin.py`` for the migration.
"""

from __future__ import annotations

import json
from pathlib import Path

from assayer_platform import PluginRegistration

from .runtime import SpecQualityPlugin
from .review import SpecQualityDecisionCommitter
from .evaluation import (
    evaluate_semantic_review_case,
    evaluate_semantic_review_corpus,
    inspect_evaluation_case,
    load_semantic_review_from_ledger,
    load_evaluation_corpus,
    validate_evaluation_corpus,
)


SPEC_QUALITY_SCOPE_SCHEMA = json.loads(
    Path(__file__).with_name("scope.schema.json").read_text(encoding="utf-8")
)


registration = PluginRegistration(
    SpecQualityPlugin.manifest,
    plugin_factory=lambda _runtime=None: SpecQualityPlugin(),
    committer_factory=lambda _runtime=None: SpecQualityDecisionCommitter(),
    capabilities=frozenset({"structured_read"}),
    result_features=frozenset({"evidence_graph"}),
    execution_modes=frozenset({"interactive"}),
    scope_schema=SPEC_QUALITY_SCOPE_SCHEMA,
)


__all__ = [
    "SpecQualityDecisionCommitter",
    "SpecQualityPlugin",
    "evaluate_semantic_review_case",
    "evaluate_semantic_review_corpus",
    "inspect_evaluation_case",
    "load_semantic_review_from_ledger",
    "load_evaluation_corpus",
    "validate_evaluation_corpus",
    "SPEC_QUALITY_SCOPE_SCHEMA",
    "registration",
]
