"""Built-in plugin registrations shipped with the Assayer distribution."""

from assayer_platform import PluginRegistration, PluginRegistry

from .frontend_audit import FrontendAuditPlugin, FrontendDecisionCommitter


def builtin_plugin_registry() -> PluginRegistry:
    """Return a fresh registry containing the distribution's built-ins.

    A fresh registry prevents one Host or test from mutating another Host's
    plugin selection.  The registry contains manifests only; runtime objects
    are still injected at the domain boundary.

    Only ``frontend-audit`` ships as a built-in.  ``ass-spec`` is an
    independently installed distribution discovered through the
    ``assayer.plugins`` entry point, and ``test.config-quality`` lives in
    ``assayer_platform.testing`` as test-only scaffolding, not a plugin.
    """
    return PluginRegistry((
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
