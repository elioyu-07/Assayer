# Implementation Slice 089 — Host-Owned Domain-Result Task Binding

Status: implemented

This slice connects the SDK v2 `DomainResultContract` to the interactive Host
without making the Agent author platform identity.

## Delivered

- Added immutable internal `TaskContext` owned by the Host.
- Added `domainResult` input to `advance_plugin_run`.
- Added Host `submit_domain_result` flow that:
  - resolves the active task context;
  - validates the frozen DomainResult Schema;
  - invokes optional `validate_domain_result`;
  - invokes required `map_domain_result` for the registered domain contract;
  - injects WorkItem and Check identity internally;
  - reuses the existing Decision/ledger commit path.
- Domain semantic tasks publish `domainContract` and a sanitized investigation
  projection without Host WorkItem, state digest, task digest, or checkpoint
  fields.
- Host rejects platform fields leaked by a plugin's result mapper.
- Transport and installed acceptance routing accept `domainResult`; the old
  checkpoint path remains only as a private Host migration primitive.

## Verification

- Domain-result contract tests cover task binding, hidden platform fields,
  Schema validation, mapper projection, and terminal completion.
- Existing contract and public-surface tests remain green.
- Python compilation and `git diff --check` pass.

## Remaining migration work

- Add page-aware domain stages so large collections do not require a complete
  WorkItem payload in one task.
- Canonicalize Agent Evidence references to Host-issued IDs only (Slice 090).
- Remove legacy checkpoint/preflight/finalization operations from the normal
  Agent catalog and reject their protocol version (Slice 091).
- Migrate the installed reference plugin and release acceptance fixtures.
