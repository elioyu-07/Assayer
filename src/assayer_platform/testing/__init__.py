"""Test-only deterministic references for exercising the platform kernel.

Nothing in this package is a shipped plugin. ``config_quality_registration``
returns a ``PluginRegistration`` for the deterministic configuration reference
so tests can build a registry without importing a browser runtime or an Agent.
"""

from __future__ import annotations

from assayer_platform import PluginRegistration

from .config_quality import ConfigQualityPlugin, ConfigurationDecisionProvider


def config_quality_registration() -> PluginRegistration:
    """Return a registration for the non-browser deterministic reference."""
    return PluginRegistration(
        ConfigQualityPlugin.manifest,
        plugin_factory=lambda _runtime=None: ConfigQualityPlugin(),
        decision_provider_factory=lambda _runtime=None: ConfigurationDecisionProvider(),
        capabilities=frozenset({"structured_read"}),
        result_features=frozenset({"evidence_graph"}),
        scope_schema={
            "type": "object", "additionalProperties": False,
            "required": ["files"],
            "properties": {
                "files": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["path"],
                        "properties": {
                            "path": {"type": "string", "minLength": 1},
                            "requiredKeys": {
                                "type": "array", "items": {"type": "string"},
                            },
                            "expectedTypes": {
                                "type": "object", "additionalProperties": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    )


__all__ = ["ConfigQualityPlugin", "ConfigurationDecisionProvider", "config_quality_registration"]
