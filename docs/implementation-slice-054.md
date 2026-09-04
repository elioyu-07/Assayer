# Vertical Slice 054: Plugin Registration and Release Conformance Gate

## Goal

Reject structurally incompatible or incomplete audit plugins before they can
enter a registry or be published in an Assayer bundle, using one reusable and
domain-neutral conformance report.

## Scope

- Add side-effect-free registration validation for manifest identity, runtime
  and batch-decision factories, capability declarations, scope schemas, and
  execution-profile safety.
- Add release validation that constructs implementations without accessing live
  providers and checks required plugin and decision-provider operations.
- Apply registration validation to built-in and installed entry points.
- Apply release validation to all bundled reference plugins before wheel build.
- Publish a stable JSON result with contract identifiers and next actions.
- Provide `assayer-plugin-check module:attribute` for source-tree release gates.

## Non-goals

- Installing or uninstalling an external plugin distribution.
- Validating semantic-review resource files or deterministic fixture contents.
- Running real providers, browsers, repositories, APIs, databases, or audits.
- Signing packages or publishing to a marketplace.

## Contract

Registration validation does not construct plugin code. It rejects missing
declarations before selection and remains safe for runtime-dependent plugins.
Release validation additionally constructs the plugin with no live runtime and,
for batch mode, its decision provider. Constructors must be deterministic and
must defer external access until an authorized Run.

Failures contain:

- a stable platform error code;
- a `PCV1-*` invariant identifier;
- a concrete message;
- a required next action.

A failed conformance report exits nonzero. The bundle builder executes the gate
before creating any wheel.

## Acceptance evidence

- All bundled reference plugins pass the same release validator.
- A missing runtime factory or batch decision provider is rejected at registry
  admission rather than failing during a user Run.
- Missing capability declarations and scope schemas are reported together.
- Invalid schemas, unsafe failure splitting, implementation identity mismatch,
  missing runtime operations, and release-load failures are deterministic.
- The conformance JSON validates against
  `schemas/plugin-conformance.schema.json`.
- The fast non-browser suite passes.

## Remaining M3 work

This slice validates registration and constructed interfaces. A later gate must
validate independently packaged resource completeness, deterministic fixtures,
semantic-review instructions, entry-point metadata, installation, upgrade,
rollback, and uninstall before the Spec plugin can be externalized.
