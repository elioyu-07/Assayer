"""Standalone frontend-audit plugin (independent from the platform kernel).

This package is the externalized Frontend audit domain.  It depends on the
Assayer platform (``assayer``) and exposes one ``assayer.plugins`` entry point,
``registration``, that the platform loads after static package validation.

The module bodies in ``runtime.py`` and ``legacy_runtime.py`` are relocated
from the platform source and use absolute ``assayer_platform`` imports; this
package is the single source of truth for the Frontend domain.
"""

from __future__ import annotations

from assayer_platform import PluginRegistration

from .runtime import FrontendAuditPlugin, FrontendDecisionProvider
from .legacy_runtime import (
    FrontendDecisionCommitter,
    FrontendLedgerCommitter,
    ProductFrontendRuntime,
)


FRONTEND_SCOPE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["url"],
    "properties": {"url": {"type": "string", "format": "uri"}},
}


registration = PluginRegistration(
    FrontendAuditPlugin.manifest,
    plugin_factory=lambda runtime: FrontendAuditPlugin(runtime),
    committer_factory=lambda caller: FrontendDecisionCommitter(caller),
    capabilities=frozenset({"structured_read", "visual_read"}),
    execution_modes=frozenset({"interactive"}),
    scope_schema=FRONTEND_SCOPE_SCHEMA,
)


__all__ = [
    "FrontendAuditPlugin",
    "FrontendDecisionProvider",
    "FrontendDecisionCommitter",
    "FrontendLedgerCommitter",
    "ProductFrontendRuntime",
    "FRONTEND_SCOPE_SCHEMA",
    "registration",
]
