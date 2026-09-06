"""Deterministic, non-browser configuration reference used only by tests.

This is not a shipped plugin: it is not registered in
``builtin_plugin_registry`` and never appears in the CLI/MCP plugin catalog.
Tests construct a registry from it directly to exercise the generic kernel,
lifecycle, and conformance paths without a browser or an Agent.
"""

from .runtime import ConfigQualityPlugin, ConfigurationDecisionProvider

__all__ = ["ConfigQualityPlugin", "ConfigurationDecisionProvider"]
