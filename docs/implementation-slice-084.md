# Implementation Slice 084 — Durable Plugin Lifecycle

Status: implemented

The platform now owns a durable, fail-closed lifecycle for independently
installed audit plugins, completing the install/discover/upgrade/rollback/
uninstall surface that M4 requires before the Spec-quality plugin can move into
an independently buildable distribution.

New platform modules (no domain knowledge, no concrete plugin import):

- `assayer_platform/plugin_installation.py` — `PluginInstallationStore`, an
  atomic on-disk index of installed plugin packages.
- `assayer_platform/plugin_lifecycle.py` — `PluginLifecycleManager`
  (install / upgrade / rollback / uninstall, each fail-closed) plus
  `discover_plugin_registry` and `load_registration` for store-backed
  discovery into a `PluginRegistry`.

Validation reuses the existing `inspect_plugin_package` static gate (no import
of plugin code) before any materialization; runtime construction happens only
at discovery. Fail-closed guarantees cover duplicate identity
(`PLUGIN_CONFLICT`), same-version re-install (`PLUGIN_VERSION_CONFLICT`),
rollback without history (`ROLLBACK_UNAVAILABLE`), and platform API or
capability mismatches through the existing conformance gates.

Covered by `tests/test_plugin_lifecycle.py` (offline, deterministic) and
documented in `docs/plugin-lifecycle-design.md`.
