# Assayer Data Schemas

This directory holds the platform-owned JSON Schema (Draft 2020-12) resources
that validate persisted platform artifacts: the compiled-plugin contract, the
design-confirmation record, provider conformance and execution, lifecycle
transactions and cleanup, parallel execution plans, and the platform ledger,
performance bill, and canonical result.

The SDK-owned plugin-facing schemas live in
`src/assayer_plugin_sdk/schemas/`. Platform validators compose both directories
into one resolver store (`assayer_platform.registry.schema_store`), so relative
references to the SDK-owned `common.schema.json` resolve without copying it into
this directory.

Every schema in this directory is loaded by a live validator. The architecture
gate (`scripts/check_architecture_boundaries.py`) rejects a schema here that no
authored code reaches, unless it is registered as an explicit author-facing
contract, and it rejects a retired v1 or legacy-plugin schema by name, so an
unreachable published surface cannot drift back in.

## Platform-owned files

- `compiled-plugin-contract.schema.json`: the one data-only ordinary-plugin
  contract a compiled declaration produces and the platform verifies.
- `design-confirmation.schema.json`: the design-change record required before a
  public, semantic, safety, or breaking change lands.
- `plugin-lifecycle-plan.schema.json`: fail-closed upgrade, rollback, and
  uninstall preconditions, ordered Codex operations, and compensation readiness.
- `plugin-lifecycle-transaction.schema.json`: sanitized durable execution
  journal for confirmed lifecycle changes, terminal verification, and
  compensation outcomes.
- `private-runtime-cleanup.schema.json`: verified post-uninstall runtime
  removal, idempotent absence, quarantined retry, and fail-closed ownership
  rejection.
- `lifecycle-acceptance.schema.json`: explicitly non-publishable isolated
  upgrade, rollback, uninstall, and runtime-cleanup acceptance result.
- `provider-conformance.schema.json`: actionable `CPV1-*` registration and
  constructed-runtime conformance results for capability providers.
- `capability-negotiation.schema.json`: four-way capability intersection, denial
  ownership, provider identity, and effective provider-budget ceilings.
- `provider-execution.schema.json`: Host-created idempotent provider requests and
  identity-bearing fact or classified-failure responses.
- `provider-release.schema.json`: external provider identity, descriptor,
  runtime source, optional installed-wheel fixture runtime factory, fixtures,
  package metadata, and conformance binding.
- `provider-fixture.schema.json`: deterministic provider fact or
  classified-failure fixture input and exact expected outcome.
- `parallel-execution.schema.json`: fail-closed serial/parallel inspection plan,
  policy reason, task count, worker ceiling, ordered merge, and failure
  isolation.
- `platform-performance-bill.schema.json`: domain-neutral measured Run, Host,
  Provider, and parallel-inspection timing; explicit unavailable Agent/model/
  transport telemetry; and estimate-only scheduler wait reduction.
- `platform-ledger.schema.json`: generic run operations, event timeline, commit
  receipts, and published artifact correlations.
- `canonical-result.schema.json`: portable terminal result, coverage, outcomes,
  findings, review items, failures, performance, and exact ledger trace
  references. Generic batch and interactive platform Runs emit and validate this
  artifact at terminal persistence.

## SDK-owned files

- `src/assayer_plugin_sdk/schemas/common.schema.json`: shared types for IDs,
  timestamps, digests, rule references, result states, severities, coordinates,
  and source locations.
- `src/assayer_plugin_sdk/schemas/plugin-manifest.schema.json`: domain-neutral
  plugin checks, evidence requirements, capabilities, and performance/recovery
  constraints.
- `src/assayer_plugin_sdk/schemas/actionable-result.schema.json`: remediation
  envelope for root-cause results that must be directly actionable, distinct
  from dimension-level Findings.
- `src/assayer_plugin_sdk/schemas/evidence-claim.schema.json`: portable direct,
  absence, derived, and external-unverified Evidence Claim contract used by
  actionable results.
- `src/assayer_plugin_sdk/schemas/capability-provider.schema.json`: provider
  identity, capability names, authorization, scope, budgets, failure semantics,
  and algorithm versions.
- `src/assayer_plugin_sdk/schemas/evaluation-corpus.schema.json`: generic
  plugin-owned fixed evaluation-corpus envelope. Domain semantics and expected
  outcomes remain in the owning plugin's packaged evaluation assets.

## Published runtime artifacts

The product runtime exports the validated platform ledger as
`platform-ledger.json`, `platform-events.jsonl`, `platform-run.log`, the
JSON/Markdown `platform-performance-bill` views, and `canonical-result.json` in
the Run output directory. The ledger JSON is canonical; JSONL is
machine-oriented, the log is a diary-like human view, and the performance bill
is a conclusion-neutral diagnostic view derived from ledger operations, events,
and metrics. The platform observability projection is
`assayer_platform.observability`.

## Retired schema surface

> **Historical note.** The v1 vertical audit ledger family (`audit-ledger`,
> `scan-run`, `page-state`, `entrypoint`, `audit-object`, `operation`,
> `dimension-finding`, `rule-assessment`, `rule-registry`, `evidence`,
> `screenshot`, `issue`, `reverse-case`, `action-attempt`,
> `request-observation`, `pending-decision`, `page-candidate`,
> `object-verification`, `derived-issues`, `page-element-judgement`,
> `run-diagnostics`, `frontend-canonical-extension`), the v1
> observability and progress contracts (`runtime-event`,
> `observability-manifest`, `performance-bill`, `public-progress`), and the
> legacy executable-plugin conformance family (`plugin-conformance`,
> `plugin-fixture`, `plugin-release`, `plugin-release-acceptance`) shipped with
> no live reader and were retired. The v1 Host semantic-validation rules, the
> v1 digest and normalization rules tied to those entities, and the protocol /
> ledger distinction described earlier versions of this file and no longer
> constrain any interface.
