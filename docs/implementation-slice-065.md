# Vertical Slice 065: Isolated Lifecycle Acceptance

## Goal

Exercise the complete release lifecycle across real process and persistence
boundaries without mutating the user's Codex configuration, installed Assayer
plugin, or private runtime.

## Acceptance topology

`scripts/isolated_lifecycle_acceptance.py` creates a temporary environment with:

- a stateful fake `codex` executable reached through the production
  `SubprocessCodexCommandRunner`;
- two immutable, attested Assayer release selectors;
- the production planner, one-use authorization store, transaction journal,
  restricted Codex client, and transaction engine; and
- an owned temporary private runtime with the production identity marker.

It then executes these independent product transitions:

1. upgrade release 0.1.0 to 0.1.1;
2. rollback release 0.1.1 to 0.1.0;
3. uninstall release 0.1.0; and
4. remove the verified uninstalled private runtime from outside that runtime.

The expected fake catalog history contains five mutations: remove/add for the
upgrade, remove/add for the rollback, and remove for uninstall. The final
catalog must contain no installed Assayer selector.

## Honesty boundary

The result conforms to `lifecycle-acceptance.schema.json` and is always marked
`mode=isolated_fake_codex` and `publishable=false`. It proves product
composition, subprocess argument boundaries, persistence, terminal
verification, and cleanup. It does not prove that a real Codex client obtained
human approval, and it never qualifies UAT-08 by itself.

Failure output exposes only a fixed code and message; temporary paths and
exception text are suppressed.

## Remaining gate

J08 still requires a supported Codex boundary that can attest authorization
origin, followed by owner-run installation, upgrade, rollback, uninstall, and
cleanup acceptance against disposable real plugin selectors. The currently
installed `assayer@personal` must not be used as that disposable target.
