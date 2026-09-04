# Vertical Slice 062: Restricted Codex Plugin Adapter

## Goal

Connect the durable lifecycle transaction engine to the supported Codex plugin
catalog, add, and remove commands without exposing a general command runner or
trusting raw CLI output.

## Fixed command surface

`CodexPluginClient` exposes only four adapter operations:

- snapshot one exact `plugin@marketplace` selector;
- add that validated selector;
- remove that validated selector; and
- verify its exact installed version, enabled state, release attestation, and
  product health.

The subprocess runner invokes no shell, accepts no arbitrary command from the
caller, closes stdin, applies bounded read and mutation timeouts, and captures
output only for validation. The only generated Codex argument vectors are:

```text
codex plugin list --json
codex plugin add <validated-selector> --json
codex plugin remove <validated-selector> --json
```

## Catalog and trust boundary

Codex remains authoritative for installed, enabled, and available state.
Assayer release attestations remain authoritative for conformance and immutable
release identity. Catalog availability alone cannot mark a package verified,
and an attestation for a different selector or version is ignored.

The adapter requires both `installed` and `available` catalog arrays. Missing,
malformed, oversized, ambiguous, or non-JSON output fails closed. It never
copies source paths, marketplace roots, stdout, or stderr into product state,
errors, or transaction journals.

Installation health is rechecked against a fresh catalog snapshot before the
injected product-health verifier runs. A missing attestation, disabled plugin,
version mismatch, verifier exception, or non-true verifier result fails health
verification.

## Uncertain command results

The adapter reports a sanitized command failure or timeout without assuming
whether the external mutation happened. `PluginLifecycleTransaction` then
reconciles the live catalog: a failed remove is accepted only when the selector
is absent, and a failed add only when the target is installed. This preserves
the no-blind-retry law across the concrete adapter.

## Acceptance evidence

Isolated tests cover exact command vectors and timeouts, selector rejection,
installed and available parsing, missing or mismatched attestations, duplicate
catalog entries, required catalog fields, invalid and oversized JSON, error
redaction, health-verifier failure, and a complete replacement transaction
against a stateful fake Codex catalog.

The official documentation endpoint was unavailable with HTTP 403 during this
slice. The fixed command interface was therefore reconciled against the
installed Codex CLI's own `plugin --help`, `plugin add --help`,
`plugin remove --help`, and `plugin list --json` contracts. No real Codex
plugin mutation was executed.

## Remaining work

Vertical Slice 063 must add durable release attestations, one-use accepted plan
tokens, and a product entrypoint that separates read-only planning from an
explicitly confirmed mutation. J08d then owns safe runtime cleanup and isolated
user-journey acceptance.
