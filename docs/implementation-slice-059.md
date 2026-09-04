# Vertical Slice 059: Read-Only Installation and Feedback Status

## Goal

Give a user or Agent one safe way to determine whether the running Assayer
release is internally consistent before starting an audit and to identify the
bounded diagnostics needed for a reproducible problem report.

## Problem

The release launcher already rejected incomplete bundles, incompatible Python
runtimes, and invalid wheel hashes. Those checks happened before MCP startup,
however, and a successful process exposed no product-level status operation.
After startup failure or a later audit failure, users and Agents had to infer
package, plugin, and private-runtime identity from configuration and local
paths. That made version mismatch diagnosis fragile and encouraged overly broad
filesystem searches.

## Product behavior

The normal product MCP now exposes `get_installation_status`. It:

- is available without an active Run;
- never starts a browser or mutates installation or Run state;
- reports package, plugin, private-runtime, Python, and platform identity;
- distinguishes `healthy`, `limited`, and `degraded` installation state;
- reports structured checks for launcher metadata, bundle verification, and
  version alignment;
- returns at most ten recent safe Run IDs with only allowlisted relative
  diagnostic artifact names; and
- tells the caller exactly what to include in a feedback report.

Source and development processes do not carry release-launcher metadata. They
report `limited` rather than pretending to be a verified release or failing a
usable development environment. A version mismatch or explicit invalid bundle
marker reports `degraded` and directs the caller to reinstall one complete
release.

## Launcher boundary

After bundle hashes and Python/platform compatibility pass, the launcher now
reads the actual Assayer distribution version from the cached private runtime
on every startup. It rejects a stale or mismatched runtime before MCP startup
and exports the verified plugin, runtime, and bundle identities to the product
process. The status operation does not trust a cache directory name as runtime
proof.

## Privacy and boundedness

The status schema contains no path field. Recent-run discovery reads only
bounded identity documents, accepts validated Run IDs, and exposes only these
artifact names when present:

- `audit-summary.md`
- `run-diagnostics.md`
- `platform-run.log`
- `result-summary.json`
- the Run-scoped platform diary name

It never returns absolute output roots, arbitrary filenames, file contents,
environment values, browser state, credentials, or raw diagnostics.

## Acceptance evidence

Deterministic tests cover aligned release metadata, absent launcher metadata,
version mismatch, schema validity, safe recent-run references, absolute-path
non-disclosure, invalid inputs, and product-MCP invocation without touching the
Host core. Packaging tests require the launcher's runtime-version check and
metadata exports.

## Remaining J08 work

This slice closes only J08a diagnosis and feedback identity. Transaction-safe
upgrade, rollback, uninstall, and cleanup through the official Codex plugin
management boundary remain separate work. No installation was mutated and no
real CLI or browser audit was started by this slice.
