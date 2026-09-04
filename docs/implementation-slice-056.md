# Vertical Slice 056: Isolated Plugin Installation and Fixture Gate

## Goal

Prove that a statically valid independent plugin package can actually be built,
installed, discovered, constructed, and exercised before it is eligible for
publication or persistent installation.

## Workflow

```text
static package conformance
  -> local build and temporary-target install
  -> new Python validation worker
  -> exact assayer.plugins entry-point discovery
  -> registration and constructed-interface conformance
  -> runtime/static manifest and scope reconciliation
  -> deterministic fixture execution through PlatformKernel
  -> actionable pass/fail report
```

`assayer-plugin-install-check PACKAGE_ROOT` never installs into the active
environment. It uses an automatically deleted target directory, disables
online index and dependency installation, removes Assayer resource and Python
path overrides, and loads only the distribution entry point found in that
target.

## Fixture authority

Fixtures are release-test inputs, not audit conclusions. Batch plugins use
their declared deterministic decision provider. Interactive plugins use a
test-only adapter that preserves plugin-provided packet states so the kernel's
evidence and decision gates are still exercised without presenting the result
as an Agent audit.

Every fixture declares the exact expected terminal status and the multiset of
decision results or failure codes. A mismatch fails publication. Fixture scope
must validate against the runtime registration's scope schema.

## Failure boundaries

The gate rejects:

- local build or temporary installation failure;
- absent, duplicated, redirected, or unimportable installed entry points;
- registration, factory, interface, capability, or scope-schema failure;
- runtime manifest or scope drift from the static release package;
- fixture scope, execution, timeout, or expected-result mismatch;
- malformed or identity-changing worker output.

Installation and fixture-worker time budgets are independently configurable
release-test controls. They do not add a total-duration cutoff to user audit
Runs.

## Security boundary

The new worker isolates import state and the temporary installation isolates
package files. This is not an operating-system sandbox: Python build backends
and plugin code can execute with the developer's process permissions. Signing,
publisher trust, and hostile-code sandboxing remain later ecosystem work.

## Acceptance evidence

- A complete source package installs in a temporary target, exposes exactly one
  matching entry point, and passes its deterministic fixture.
- A false fixture expectation fails with
  `PLUGIN_FIXTURE_EXPECTATION_MISMATCH`.
- Runtime manifest drift fails with `PLUGIN_INSTALLED_MANIFEST_MISMATCH`.
- A package that installs an additional plugin entry point fails before runtime
  construction.
- Plugin-focused and complete fast non-browser suites pass.

## Remaining work

The three release checks now cover registration, static package resources, and
isolated execution. Persistent user installation, atomic upgrade/rollback,
uninstall, and signed publisher trust belong to M4/M7. The next active task
returns to the remaining J04-J08 user-journey gates before externalizing the
Spec plugin.
