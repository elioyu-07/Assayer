# Implementation Slice 091 — Remove Legacy Agent Platform Operations

Status: implemented

This slice applies the hard-cut rule at the normal interactive Agent boundary.
The Host may retain the old state-machine methods internally while the Agent
can no longer discover or invoke them.

## Delivered

- Removed `checkpoint_review`, `validate_checkpoint_draft`,
  `submit_decisions`, and `finish_plugin_run` from the interactive MCP tool
  catalog and input schemas.
- Removed their legacy construction guidance from the Agent-facing tool
  descriptions.
- Rejected direct calls to those names with `UNSUPPORTED_PROTOCOL`.
- Rejected legacy `reviewCheckpoint`, `decision`, and `closeout` envelopes on
  `advance_plugin_run` before schema translation or Host binding.
- Kept the corresponding controller methods private migration primitives only;
  they are not reachable through the normal Agent catalog.
- Rewrote the installed Assayer Agent Skill and plugin-development guidance to
  teach `domainResult` submission and Host-owned lifecycle state only.

## Verification

- Added hard-cut surface tests for catalog absence and unsupported-protocol
  responses.
- P3 Evidence and DomainResult tests remain green.
- Python compilation and `git diff --check` pass.

## Follow-up boundary work

- DomainResult release acceptance is enforced by Slice 092.
- Large domain datasets still need domain-stage paging when a plugin chooses to
  split one business review across multiple bounded DomainResult tasks. This is
  a domain contract extension, not a return of platform checkpoint fields.
