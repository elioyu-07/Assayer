# Independent Plugin Lifecycle — Design

Status: implemented

## Purpose

Stage 3 (M4) proves that a domain audit plugin can be installed, discovered,
upgraded, rolled back, and uninstalled independently of the platform source.

The existing pieces already cover two adjacent concerns:

- **Conformance gates** (`inspect_plugin_package`, `inspect_plugin_installation`)
  validate an independent plugin package without importing its code and run its
  deterministic fixtures inside an isolated target. They install to a throw-away
  directory and discard it.
- **Product-transport lifecycle** (`assayer_host/lifecycle_*`, `codex_plugin_client`)
  plans and compensates `upgrade`/`rollback`/`uninstall` of the Assayer product
  plugin through the Codex CLI marketplace.

What is missing is the **platform-owned, durable lifecycle for domain plugins**:
a place where the platform records what it installed, plus the install / upgrade
/ rollback / uninstall operations that persist across processes and fail closed.

## Scope

Two new platform modules (no domain knowledge, no concrete plugin import):

1. `assayer_platform/plugin_installation.py` — durable, atomic on-disk index of
   installed plugin packages (`PluginInstallationStore`).
2. `assayer_platform/plugin_lifecycle.py` — the fail-closed operations that
   orchestrate validation, materialization, and index mutation
   (`PluginLifecycleManager`) plus store-backed discovery that merges installed
   plugins into a `PluginRegistry`.

## Index model

`PluginInstallationStore` persists one `index.json` under its root, written
atomically (temporary file + `replace`), with this shape:

```json
{
  "schemaVersion": "1.0.0",
  "plugins": {
    "<pluginId>": {
      "activeVersion": "1.2.0",
      "history": ["1.0.0", "1.1.0", "1.2.0"],
      "versions": {
        "1.2.0": {
          "installedAt": 1700000000,
          "packageRoot": "packages/<pluginId>/1.2.0",
          "conformance": { "...": "passed" }
        }
      }
    }
  }
}
```

- `history` records the activation order (oldest first, most recent last).
  `activeVersion` is `history[-1]`.
- Each `versions.<v>.packageRoot` points at the materialized package source so it
  can be re-discovered and re-verified without a network install.

## Operations (all fail closed)

`PluginLifecycleManager` takes a store plus an injectable `installer`
(`Callable[[Path, Path], None]`) and `registration_loader`
(`Callable[[Path], PluginRegistration]`). Injecting both keeps the fast test
suite offline and deterministic.

- **install(package_root)**
  1. `inspect_plugin_package` (static, no import of plugin code). Reject on any
     issue with `PLUGIN_PACKAGE_INVALID`.
  2. Reject `PLUGIN_CONFLICT` if the plugin id is already installed.
  3. Materialize the package into `store/packages/<id>/<version>`.
  4. Persist the index atomically; activate the new version.

- **upgrade(package_root)**
  1. Same validation as install; require the plugin id to already exist.
  2. Reject `PLUGIN_VERSION_CONFLICT` if the version is already installed.
  3. Materialize into a **new** version directory. The previous version stays
     untouched, so a failure before activation leaves the old version intact.
  4. Atomically append the new version to history and flip `activeVersion`.

- **rollback(plugin_id)**
  1. Require at least two history entries, else `ROLLBACK_UNAVAILABLE`.
  2. Drop the last history entry and set `activeVersion` to the new tail. The
     rolled-back version directory is preserved for a later re-upgrade.

- **uninstall(plugin_id)**
  1. Remove every version directory and the index entry atomically.
  2. Built-in plugins are never touched, so the platform still starts.

- **list() / get(plugin_id)** — read-only.

## Discovery

`discover_plugin_registry(store, *, builtins=())` builds a `PluginRegistry` from
the built-ins plus every installed plugin's active version, loading each
registration through the injectable loader (default: import the declared
`runtimeSource` module). Conflicts (duplicate id, capability-missing, platform
API mismatch) fail closed through the existing `PluginRegistry.register` and
`require_plugin_registration_conformance`.

## Fail-closed guarantees (task 15)

- Same-version re-install → `PLUGIN_CONFLICT` / `PLUGIN_VERSION_CONFLICT`.
- Platform API incompatibility → rejected by `load_plugin_manifest`
  (`PLUGIN_API_INCOMPATIBLE`) during static validation.
- Capability-missing → rejected by `inspect_plugin_registration`
  (`PLUGIN_CAPABILITY_UNDECLARED`) before registration.
- Ambiguous selection → existing `PluginRegistry.select` (`PLUGIN_AMBIGUOUS`).
