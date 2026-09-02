"""Read-only Markdown Spec quality plugin."""

from .runtime import SpecQualityPlugin
from .review import SpecQualityDecisionCommitter

__all__ = ["SpecQualityDecisionCommitter", "SpecQualityPlugin"]
