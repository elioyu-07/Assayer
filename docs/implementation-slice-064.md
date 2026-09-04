# Vertical Slice 064: Verified Private-Runtime Cleanup

## Goal

Remove an uninstalled Assayer release's reproducible private runtime without
letting an Agent, malformed transaction, path substitution, or interrupted
deletion broaden the cleanup target.

## Ownership marker

The release launcher now writes `runtime-identity.json` only after wheel,
platform, Python, package, and runtime-version verification succeeds. The
atomic marker contains only:

- schema version;
- complete Codex plugin version; and
- Python distribution version.

It contains no absolute path or environment value. Existing valid runtimes
receive the marker on their next verified launch.

## Cleanup gate

`PrivateRuntimeCleaner` derives the only possible target from trusted cache
configuration and the current release version in a schema-valid transaction.
It accepts cleanup only when all of these facts hold:

- the transaction operation is `uninstall`;
- the transaction is `completed` with the normal lifecycle completion code;
- its final Codex catalog snapshot identifies the same selector and version as
  absent and disabled;
- the runtime directory name is derived from the validated release version;
- neither the Assayer root nor the runtime target is a symbolic link; and
- the runtime identity marker exactly matches the transaction release.

No path is accepted from an Agent or lifecycle tool call. The result exposes a
relative runtime reference only.

## Interruption behavior

The cleaner first atomically renames the owned runtime to a transaction-specific
quarantine directory, then deletes it. If deletion stops, a retry recognizes
and removes only that same quarantined directory. If both active and
quarantined directories exist, cleanup rejects the ambiguous state and keeps
both. An already absent runtime is an idempotent terminal result.

The cleaner must be invoked by a trusted process outside the runtime being
removed. It is intentionally not called from the Assayer MCP process whose
interpreter and imports live in that runtime.

## Product authorization boundary

The installed MCP SDK exposes elicitation, but its local contract permits an
Agent rather than a human to answer. Elicitation therefore cannot satisfy the
trusted external authorization required by Slice 063. Slice 064 does not wire
destructive lifecycle execution into the shipped launcher on that basis.

J08 remains open until a Codex client boundary can establish the source of
approval, invoke the existing controller, stop the old MCP runtime, run this
cleaner externally, and prove the complete flow in an isolated installation.

## Acceptance evidence

Isolated tests cover exact owned-runtime removal, sibling preservation,
terminal-uninstall proof, current-selector absence, missing identity,
symbolic-link rejection, idempotent absence, interrupted deletion retry, and
ambiguous-root rejection. No user cache, installed plugin, browser, or real
Codex add/remove command is touched.
