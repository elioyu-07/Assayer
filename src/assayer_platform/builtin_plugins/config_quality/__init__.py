"""Read-only configuration quality plugin used as the non-browser reference."""

from .runtime import ConfigQualityPlugin, ConfigurationDecisionProvider

__all__ = ["ConfigQualityPlugin", "ConfigurationDecisionProvider"]
