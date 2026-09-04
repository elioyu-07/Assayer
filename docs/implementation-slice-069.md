# Vertical Slice 069: External Provider Release Gate

## Goal

Make capability providers independently packageable without weakening the
platform contract. A provider release must pass registration, static package,
and isolated runtime checks before publication or persistent installation.

## Independent release shape

Every provider source package contains `assayer-provider-release.json`. The
release descriptor binds one provider identity and version to:

- a validated capability-provider descriptor;
- one `assayer.providers` registration import path;
- the packaged Python runtime source root;
- deterministic fact and classified-failure fixtures;
- Python package metadata and an explicit Assayer dependency; and
- the frozen provider conformance contract version.

Provider packages do not contain plugin rules or semantic-review instructions.
Providers supply controlled facts; audit plugins own domain interpretation.

## Static package gate

`assayer-provider-package-check` reads only package data and filesystem
metadata. It never imports provider code, constructs the runtime, accesses a
source, or changes the active environment.

The gate rejects unsafe or symbolic-link paths, missing resources, descriptor
or API incompatibility, cross-file identity drift, absent registration source,
invalid fixture shape or scope, unknown capabilities or Evidence kinds,
duplicate fixture IDs, missing platform dependencies, version mismatch, and
missing `assayer.providers` entry points.

Every declared capability requires at least one deterministic successful fact
fixture. The package must also contain at least one classified-failure fixture.
This prevents a multi-capability provider from publishing after testing only a
convenient subset.

## Isolated installation gate

`assayer-provider-install-check` first requires a passing static report, then:

1. installs the local source package into an automatically deleted target with
   network index and dependency installation disabled;
2. launches a new Python worker with user-site and path overrides disabled;
3. requires exactly one matching `assayer.providers` entry point from the
   isolated distribution;
4. reruns constructed provider conformance without calling a live source;
5. reconciles the runtime descriptor with the statically checked descriptor;
6. executes every fixture through negotiated `BoundCapabilityProvider`; and
7. compares exact Evidence kinds and counts or classified failure codes.

The install and fixture-worker time limits protect this release test. They do
not introduce a total timeout for user audit Runs.

## Security boundary

Temporary installation and a new process isolate package state and import
caches. They are not an operating-system sandbox. Python build backends and
provider fixtures execute with the developer process permissions, so only
reviewed local source packages belong in this gate. Signing, publisher trust,
and hostile-code isolation remain ecosystem work.

## Acceptance evidence

Deterministic tests cover static validation without code import, path escape,
identity and version mismatch, entry-point mismatch, fixture capability and
coverage checks, isolated success and failure execution, runtime descriptor
drift, result mismatch, and duplicate installed entry points.

## Remaining boundary

Provider registration, negotiation, execution, Evidence binding, static
packaging, and isolated installation now share enforceable contracts.
Persistent user installation, upgrade and rollback orchestration, publisher
trust, and migration of existing product adapters remain later work.
