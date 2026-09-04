# Vertical Slice 055: Independent Plugin Package Resource Gate

## Goal

Reject an incomplete, inconsistent, or path-unsafe independent plugin package
before importing its code or attempting installation.

## Package descriptor

Every package root contains `assayer-plugin-release.json`, validated by
`schemas/plugin-release.schema.json`. It binds:

- plugin ID, plugin version, and supported platform API version;
- the `assayer.plugins` registration import path;
- the plugin manifest and top-level business-input scope schema;
- the Python runtime source root;
- nonempty Markdown semantic-review instructions;
- one or more deterministic conformance fixtures;
- `pyproject.toml` package metadata;
- the conformance contract version.

Each deterministic fixture conforms to `schemas/plugin-fixture.schema.json`
and declares a stable fixture ID, a manifest Check, business scope, and the
expected terminal decision results or failure codes.

## Safety and validation order

`assayer-plugin-package-check PACKAGE_ROOT` performs only data and filesystem
inspection. It does not add the package to `sys.path`, import its registration,
construct its runtime, install dependencies, or invoke a provider.

The gate rejects:

- missing or malformed descriptors and resources;
- absolute, escaping, or symbolic-link resource paths;
- manifest/API incompatibility and descriptor identity mismatch;
- invalid or non-object scope schemas;
- absent registration source modules;
- empty or non-Markdown semantic-review instructions;
- invalid, duplicate, or unknown-Check fixtures;
- missing `assayer.plugins` entry points and version mismatch.

Every failure uses the shared plugin conformance report and provides a stable
error code, `PCV1-PACKAGE-RESOURCES`, and a required next action.

## Acceptance evidence

- A complete package passes without importing code.
- Descriptor path traversal is rejected before resource access.
- Identity, fixture Check, project version, and entry-point mismatches are
  reported deterministically.
- Release and fixture schemas are exercised by contract tests.
- The plugin-focused and complete fast non-browser suites pass.

## Remaining gate

Static completeness is not installation proof. The next M3 slice installs a
validated package into an isolated environment, discovers only its declared
entry point, runs registration/release conformance and deterministic fixtures,
and refuses promotion when any step fails.
