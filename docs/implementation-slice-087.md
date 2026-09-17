# Implementation Slice 087 — Public Plugin SDK Boundary and Semantic Contract Enforcement

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


Status: implemented; release and operator-level natural-language acceptance remain separate gates

This slice closes the platform-owned SDK boundary for independently packaged
plugins and makes schema-external Agent rejection rules part of the frozen,
executable contract.

## Public SDK

`assayer_platform.plugin_sdk` is now a versioned public surface (`1.2.0`) and
exports:

- `validate_entity_id` with the shared platform EntityId grammar;
- `to_json_value` for deterministic, recursively JSON-safe plugin values;
- `PluginContractError` and the stable `PLUGIN_CONTRACT_VIOLATION` default code.

The projection rejects non-string object keys, non-finite numbers, cycles,
bytes, unknown objects, and silently coercive output. Sets are detached into
deterministically ordered arrays. The Host applies the same boundary before
terminal summary publication.

## Executable Agent contracts

`AgentContractBundle` now accepts immutable `checkpointSemanticRules`. Rules
are collection-scoped, require unique stable `ruleId` values and executable
instructions, and participate in the contract digest. The interactive Host
publishes only the current collection's schema and rules, binds submissions to
the exact `taskDigest`, and maps stale boundaries, plugin defects, and runtime
failures to bounded non-automatic retry policies.

The interactive Host also exposes `validate_checkpoint_draft` as a mandatory
read-only preflight for checkpoint payloads. It uses the same frozen-task,
collection, JSON Schema, and plugin semantic-validator path as
`checkpoint_review`, but never persists a checkpoint, records a rejection, or
consumes correction budget. Skills must repair a `valid: false` draft from its
structured error and preflight again before making the mutating submission.
The Host rejects direct `checkpoint_review` calls and changed-payload replays;
there is no compatibility path around preflight.
When a caller bypasses that preflight and exhausts the correction budget, the
terminal error preserves the original validation pointers/messages.

Terminal plugin contract violations, malformed summaries, unexpected plugin
exceptions, and invalid assembled deliveries close safely without persistence
or fabricated Decisions. A second Run in the same live transport requires
explicit user authorization.

## Lifecycle alignment

Lifecycle MCP now reports catalog version relationships in one bounded
read-only lookup, including `current`, `update_available`, and
`installed_ahead_of_catalog`. The lifecycle Skill documents the same contract
and rerun rules.

## Verification

- Focused SDK/contract/lifecycle tests: 150 passed.
- Fast deterministic gate: 742 passed.
- Full browser/MCP gate: 769 passed.
- The exact Assayer bundle built successfully, installed its exact
  `assayer-0.1.2` wheel into a clean temporary environment without network
  access, exposed 17 MCP tools, and started the product launcher.
- The exact `ass_spec-2.0.1` wheel (`sha256:2eb946d5ca78aef73b90f693df7a62ac8f4d329c6cd14a8dab8f0878b00788ed`)
  passed `source_static`, `public_surface`, `wheel_artifact`, and
  `installed_lifecycle` against the exact `assayer-0.1.2` wheel.

## Remaining acceptance

The independent SDK consumer gate is now complete: exact `assayer-0.1.2` and
`ass_spec-2.0.1` wheels pass the four-stage release gate, and the plugin's 64
tests pass in a clean environment. The plugin requires `assayer>=0.1.2`, so an
old `assayer 0.1.1` installation is rejected rather than being treated as
compatible.

This slice still does not claim the operator-owned Clean Codex Marketplace
journey. The disposable Codex CLI gate is now complete: marketplace discovery,
installation, version inspection, and clean installed-bundle MCP startup all
passed with `codex-cli 0.153.0` and
`0.1.2+codex.20260909095928`. The remaining operator evidence must record an
interactive Codex session installing and running an independent domain plugin,
upgrading/rolling back, uninstalling, and observing durable state after
restart through natural-language confirmation flows.
