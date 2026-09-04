# Vertical Slice 063: One-Use Lifecycle Authorization

## Goal

Separate lifecycle planning from installation mutation so an Agent can first
show an exact upgrade, rollback, or uninstall plan, the user can confirm that
plan, and a trusted client boundary can independently authorize execution.

## Product flow

The optional lifecycle product surface contains two tools:

1. `plan_plugin_change` reads current installation and release facts, applies
   the fail-closed planner, and performs no mutation. A ready plan receives a
   bounded one-use token.
2. The client displays the returned plan and requests explicit user approval.
3. `execute_plugin_change` accepts only that token and `confirmed=true`. The
   controller also calls an injected trusted authorizer. Model-supplied
   confirmation alone is never installation authority.
4. The token is atomically claimed before the transaction engine can mutate
   Codex plugin state. The terminal journal and authorization record then make
   retries deterministic.

The MCP tool metadata marks planning as non-destructive and execution as
destructive and idempotent. The lifecycle tools are absent unless a trusted
controller is explicitly injected. The normal shipped launcher does not yet
inject that controller; J08d owns product wiring and isolated acceptance.

## Durable token contract

`LifecyclePlanStore` keeps authorization state in SQLite with full synchronous
durability and write-ahead logging. It stores only the SHA-256 digest of the
random bearer token. The raw token is returned once to the caller and is not
written into the store.

A token may be in one of four durable states:

- `awaiting_confirmation`: a ready plan may still be displayed and approved;
- `executing`: exactly one claimant owns execution;
- `terminal`: the stored transaction result is replayed without another
  authorization or mutation; or
- `expired`: the plan can no longer mutate installation state.

Tokens live for 30 minutes by default and the supported bound is 60 through
3,600 seconds. An authorization rejection does not claim the token. An
authorization callback exception fails closed and reveals no callback detail.

## Interruption and unknown results

The transaction ID is deterministically derived from the token digest. If a
process stops after claiming a token, a later request checks that transaction
journal. A terminal journal repairs the token record and returns the stored
result. A missing or non-terminal journal returns `result_unknown`; it does not
blindly repeat external mutations.

## Published result contract

`plugin-lifecycle-product.schema.json` validates the complete planning and
execution result union. It closes unknown fields, binds nested plans and
transactions to their existing schemas, distinguishes terminal replay from
non-terminal guidance, and prevents controller-private data from entering the
MCP response.

## Acceptance evidence

Isolated tests cover ready and blocked planning, raw-token non-persistence,
expiration, external authorization absence and failure, atomic claims across
two store instances, terminal replay, interrupted execution repair, malformed
input and output rejection, optional product-tool exposure, and preserved MCP
destructive/idempotent annotations.

No real Codex plugin add or remove command was executed. Real product wiring,
private-runtime cleanup, and install/upgrade/uninstall acceptance remain J08d.
