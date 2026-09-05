"""Standalone Touchstone audit plugin (independent distribution).

This package is the externalized Spec domain. It depends on the Assayer
platform (``assayer``) and exposes one ``assayer.plugins`` entry point,
``registration``, that the platform loads after static package validation.

The module bodies in ``runtime.py``, ``review.py``, and ``evaluation.py`` are
relocated from the platform source and use absolute ``assayer_platform``
imports; this package is the single source of truth for the Spec domain.
"""

from __future__ import annotations

import json
from pathlib import Path

from assayer_platform import PluginRegistration

from .runtime import TouchstonePlugin
from .review import TouchstoneDecisionCommitter
from .evaluation import (
    evaluate_semantic_review_case,
    evaluate_semantic_review_corpus,
    inspect_evaluation_case,
    load_semantic_review_from_ledger,
    load_evaluation_corpus,
    validate_evaluation_corpus,
)


TOUCHSTONE_SCOPE_SCHEMA = json.loads(
    Path(__file__).with_name("scope.schema.json").read_text(encoding="utf-8")
)


registration = PluginRegistration(
    TouchstonePlugin.manifest,
    plugin_factory=lambda _runtime=None: TouchstonePlugin(),
    committer_factory=lambda _runtime=None: TouchstoneDecisionCommitter(),
    capabilities=frozenset({"structured_read"}),
    result_features=frozenset({"evidence_graph"}),
    execution_modes=frozenset({"interactive"}),
    scope_schema=TOUCHSTONE_SCOPE_SCHEMA,
)


__all__ = [
    "TouchstoneDecisionCommitter",
    "TouchstonePlugin",
    "evaluate_semantic_review_case",
    "evaluate_semantic_review_corpus",
    "inspect_evaluation_case",
    "load_semantic_review_from_ledger",
    "load_evaluation_corpus",
    "validate_evaluation_corpus",
    "TOUCHSTONE_SCOPE_SCHEMA",
    "registration",
]
