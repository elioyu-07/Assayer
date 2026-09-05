"""Read-only Markdown Spec quality plugin."""

import json
from pathlib import Path

from .runtime import SpecQualityPlugin
from .review import SpecQualityDecisionCommitter
from .evaluation import (
    evaluate_semantic_review_case, evaluate_semantic_review_corpus,
    inspect_evaluation_case, load_semantic_review_from_ledger,
    load_evaluation_corpus, validate_evaluation_corpus,
)


SPEC_QUALITY_SCOPE_SCHEMA = json.loads(
    Path(__file__).with_name("scope.schema.json").read_text(encoding="utf-8")
)

__all__ = [
    "SpecQualityDecisionCommitter", "SpecQualityPlugin",
    "evaluate_semantic_review_case", "evaluate_semantic_review_corpus",
    "inspect_evaluation_case", "load_semantic_review_from_ledger",
    "load_evaluation_corpus", "validate_evaluation_corpus",
    "SPEC_QUALITY_SCOPE_SCHEMA",
]
