"""Route natural-language plugin requests to the correct product workflow.

The router does not execute mutations or Runs.  It only separates the Assayer
Codex product lifecycle from domain-plugin lifecycle and plugin usage, then
delegates single-step parsing to :mod:`plugin_intent`.  Execution remains in
the existing Host transports and therefore retains their confirmation gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .plugin_intent import IntentResolutionError, IntentStep, resolve_intent


@dataclass(frozen=True)
class PluginRoute:
    """A non-executing route decision for one natural-language request."""

    workflow: str
    steps: tuple[IntentStep, ...] = ()
    message: str | None = None

    def as_dict(self) -> dict:
        value = {"workflow": self.workflow, "steps": [step.as_dict() for step in self.steps]}
        if self.message is not None:
            value["message"] = self.message
        return value


def route_plugin_request(
    text: str,
    *,
    known_plugin_ids: tuple[str, ...] = (),
    cwd: Path | None = None,
) -> PluginRoute:
    """Classify a request without mutating state or invoking a plugin.

    Assayer itself is owned by Codex Marketplace/product lifecycle.  Domain
    plugin requests use the lifecycle MCP or interactive Run workflow.  A
    compound "install ... then run ..." request is represented as two ordered
    intent steps and is still subject to the mutation confirmation gate.
    """
    lowered = text.strip().lower() if isinstance(text, str) else ""
    if not lowered:
        raise IntentResolutionError("INTENT_EMPTY", "Provide a natural-language request.")

    # A local package path may contain the repository name "Assayer". Treat it
    # as a product request only when the natural-language segment has no path.
    product_request = "assayer" in lowered and "/" not in text and "\\" not in text
    if product_request and not any(plugin.lower() in lowered for plugin in known_plugin_ids):
        if any(word in lowered for word in ("install", "add")):
            return PluginRoute("product_install", message="Install Assayer through Codex Marketplace.")
        if any(word in lowered for word in ("upgrade", "update")):
            return PluginRoute("product_upgrade", message="Upgrade Assayer through Codex Marketplace.")
        if any(word in lowered for word in ("rollback", "roll back", "downgrade")):
            return PluginRoute("product_rollback", message="Roll back Assayer through Codex Marketplace.")
        if any(word in lowered for word in ("uninstall", "remove", "delete")):
            return PluginRoute("product_uninstall", message="Uninstall Assayer through Codex Marketplace.")

    compound_markers = (" then ", " and then ", " after installing ")
    marker = next((marker for marker in compound_markers if marker in lowered), None)
    if marker is not None and any(word in lowered for word in ("install", "add")):
        split_at = lowered.index(marker)
        install_text, run_text = text[:split_at], text[split_at + len(marker):]
        install = resolve_intent(install_text, known_plugin_ids=known_plugin_ids, cwd=cwd)
        if "use" in run_text.lower() and install[0].plugin_id:
            run_text = run_text.replace("use", f"run {install[0].plugin_id}", 1)
        run = resolve_intent(run_text, known_plugin_ids=known_plugin_ids, cwd=cwd)
        if install[0].operation != "install" or run[0].operation != "run":
            raise IntentResolutionError(
                "INTENT_COMPOUND_UNSUPPORTED",
                "Only install-then-run plugin requests can be combined.",
            )
        return PluginRoute("install_then_run", steps=install + run)

    steps = resolve_intent(text, known_plugin_ids=known_plugin_ids, cwd=cwd)
    workflow = "plugin_run" if steps[0].operation == "run" else "plugin_lifecycle"
    return PluginRoute(workflow, steps=steps)


__all__ = ["PluginRoute", "route_plugin_request"]
