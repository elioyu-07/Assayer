# Vertical Slice 061: Durable Lifecycle Transaction Engine

## Goal

Execute one accepted plugin lifecycle plan with durable state, immediate
precondition reconciliation, terminal verification, and automatic
compensation without coupling transaction safety to Agent memory.

## Transaction boundary

`PluginLifecycleTransaction` consumes a schema-valid plan from Vertical Slice
060 and an injected plugin-management adapter. Before any adapter mutation it:

1. requires explicit user confirmation;
2. re-reads current and target installation state;
3. rechecks the active-Run guard and release direction;
4. rebuilds the entire plan and rejects any stale difference; and
5. durably persists the running journal.

A blocked plan, absent confirmation, changed version, changed release facts,
or newly active Run performs no mutation.

## Execution and compensation

For an accepted replacement, the engine removes the previous selector,
installs the target, verifies exact version and enabled state through the
adapter, checks Assayer installation health, and confirms the previous selector
is absent. For uninstall it removes the selector and verifies absence.

Once any mutation may have occurred, every failure enters compensation. A
partially installed target is removed, the immutable previous selector is
restored, and its version, enabled state, and installation health are verified.
The terminal state is:

- `completed` when forward verification succeeds;
- `rolled_back` when forward execution fails but restoration succeeds; or
- `failed` when restoration cannot be proved.

An external operation whose response is lost is reconciled by reading live
adapter state. A remove error is accepted only if the selector is provably
absent; an add error is accepted only if the target is provably installed.
Blind retries are not used.

## Journal safety

The transaction journal is validated by
`plugin-lifecycle-transaction.schema.json` and atomically written before
mutation and after each durable boundary. It contains only validated selector
and version identities, a plan digest, fixed action names, sanitized outcome
codes/messages, and final installation state. It does not contain commands,
stdout, stderr, paths, environment values, or secrets.

If the initial journal cannot be persisted, execution stops before mutation.
If a later journal write fails after removal, the failure still triggers
compensation; logging failure is never mistaken for proof that no mutation
occurred.

## Acceptance evidence

Deterministic adapter fault tests cover:

- missing confirmation;
- successful replacement and uninstall;
- stale plans and active Runs;
- target add failure;
- target health failure;
- successful and failed compensation;
- initial journal failure before mutation;
- journal failure immediately after current removal;
- terminal journal replay and schema validity; and
- suppression of raw exception details.

## Remaining work

J08c2 must implement the concrete Codex adapter using only supported plugin
catalog, add, and remove commands, connect accepted conformance receipts and
immutable release metadata, and expose an explicit-confirmation product
entrypoint. J08d will add safe private-runtime cleanup and isolated acceptance.
No real Codex plugin installation was changed by this slice.
