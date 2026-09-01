# Vertical Slice 001: Protocol and Operation Core

## Scope

This slice implements deterministic Host Core boundaries without a browser: Bootstrap/Session envelope and tool-input validation; Scan/Run identity and `expectedRunRevision` gates; Operation idempotency, request digests, and `IDEMPOTENCY_CONFLICT`; read-only `get_operation`; SQLite persistence across restarts; one-time TTL credential handles and replaceable login adapters; and fail-closed `INTERNAL_FAILURE` when browser adapters are absent.

Implementation: [core.py](../src/assayer_host/core.py). Tests: [test_core.py](../tests/test_core.py). Bootstrap output is validated against its output schema so Host cannot emit an Agent-unconsumable result.

## Out of Scope

Browser navigation, object discovery, request interception, ledger events, Case recovery, and formal decision commit are deferred. This slice persists startup/Operation metadata only and cannot claim those capabilities.

## Next-Slice Gate

Add persistent event storage without changing idempotency; connect `start_audit` to one-time credential/login adapters; add a read-only page adapter; and add MCP/CLI transport contract tests.

## Slice 002 Startup Transaction

`start_audit` validates envelope, inputs, and registry version; atomically writes an `authenticating` Scan, `running` Bootstrap Operation, and global bootstrap key; consumes the TTL credential handle as an ephemeral adapter argument; updates to `exploring/succeeded` on login success or `failed/failed_known` on invalid handle, login failure, or adapter error; and returns the same persisted Scan/Operation after restart without re-consuming credentials.
