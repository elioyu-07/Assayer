# Vertical Slice 060: Fail-Closed Plugin Lifecycle Planning

## Goal

Define the transaction boundary for Assayer upgrade, rollback, and uninstall
before any code is allowed to mutate a user's Codex plugin configuration.

## Why planning is a separate slice

Codex owns plugin installation state. Assayer must use the supported
`codex plugin add`, `codex plugin remove`, and plugin catalog interfaces rather
than edit Codex configuration or cache files itself. Those commands are
individually authoritative, but a multi-command replacement still needs
Assayer-owned preconditions, ordering, verification, and compensation.

A naive attempt to install a new Assayer beside the old one is not a safe
staging strategy. Both releases can contribute the same Skill and MCP names.
The plan therefore validates both release packages outside the active Codex
configuration, removes the current selector only after a rollback source is
ready, installs the target, and compensates by restoring the previous selector
if target verification fails.

## Planning contract

`PluginLifecyclePlanner` is read-only. It accepts sanitized catalog facts and
produces a `plugin-lifecycle-plan.schema.json` document with:

- the requested `upgrade`, `rollback`, or `uninstall` operation;
- `ready`, `blocked`, or idempotent `noop` status;
- current and target selector/version identity;
- explicit pass/fail preconditions;
- ordered forward steps;
- ordered compensation steps; and
- one actionable next step.

The planner never invokes Codex, changes configuration, deletes cache data, or
starts an Assayer Run.

## Replacement gates

Upgrade and rollback are blocked unless all of these facts are true:

- no Assayer Run is active;
- the current plugin is installed and enabled;
- current and target refer to the same plugin name through distinct selectors;
- both current and target releases have immutable marketplace sources;
- both releases have accepted conformance records;
- the target is available but not already installed;
- current and target versions differ; and
- the release catalog proves the requested upgrade or rollback direction.

Using separate immutable selectors is intentional. A mutable selector such as
`assayer@personal` cannot prove that the exact previous bytes will still be
available after an upgrade and therefore cannot claim rollback readiness.

## Forward and compensation order

For upgrade and rollback, the forward sequence is:

1. Re-read and verify all preconditions.
2. Remove the current selector with Codex.
3. Install the target selector with Codex.
4. Verify target identity, enabled state, and Assayer installation health.
5. Verify the terminal catalog state.

If any step after removal fails, the compensation sequence removes a partially
installed target, restores the immutable previous selector, and verifies the
restored installation. A plan says only that compensation is ready before
mutation; it does not falsely guarantee that an external command can never
fail.

Uninstall similarly requires an immutable verified restoration source before
mutation. An already absent plugin returns `noop`. Runtime-cache cleanup is not
part of uninstall planning and must not run until Codex confirms that the
plugin is absent.

## Acceptance evidence

Deterministic tests cover upgrade and rollback ordering, compensation
readiness, active-Run blocking, mutable-source rejection, unverified-release
rejection, duplicate-name prevention, uninstall guards, idempotent absence,
schema validity, and unsafe selector/version rejection.

## Remaining work

J08c must build a Codex adapter that derives these facts from the product
release catalog, requests explicit user confirmation immediately before
mutation, executes exactly one accepted plan, persists a sanitized lifecycle
journal, verifies the final installation, and automatically runs compensation
when required. J08d will add post-uninstall private-runtime cleanup and isolated
acceptance. This slice performed no real plugin mutation.
