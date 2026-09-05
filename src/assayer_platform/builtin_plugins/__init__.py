"""Built-in plugin registrations shipped with the Assayer distribution."""

from assayer_platform import PluginRegistration, PluginRegistry

from .config_quality import ConfigQualityPlugin, ConfigurationDecisionProvider
from .frontend_audit import FrontendAuditPlugin, FrontendDecisionCommitter


def builtin_plugin_registry() -> PluginRegistry:
    """Return a fresh registry containing the distribution's built-ins.

    A fresh registry prevents one Host or test from mutating another Host's
    plugin selection.  The registry contains manifests only; runtime objects
    are still injected at the domain boundary.  The Spec-quality plugin is no
    longer a built-in; it is shipped as an independently installed
    distribution and discovered through the ``assayer.plugins`` entry point.
    """
    return PluginRegistry((
        PluginRegistration(
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
        ),
        PluginRegistration(
            FrontendAuditPlugin.manifest,
            plugin_factory=lambda runtime: FrontendAuditPlugin(runtime),
            committer_factory=lambda caller: FrontendDecisionCommitter(caller),
            capabilities=frozenset({"structured_read", "visual_read"}),
            execution_modes=frozenset({"interactive"}),
            scope_schema={
                "type": "object", "additionalProperties": False,
                "required": ["url"],
                "properties": {"url": {"type": "string", "format": "uri"}},
            },
        ),
    ))


def installed_plugin_registry() -> PluginRegistry:
    """Return built-ins plus registrations from installed distributions."""
    return PluginRegistry.from_entry_points(
        registrations=builtin_plugin_registry().list(),
    )


__all__ = ["builtin_plugin_registry", "installed_plugin_registry"]
