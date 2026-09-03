# Platform v1 Contract Traceability

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-03 |
| Status | M2 baseline; M3 enforcement backlog |
| Owner | Assayer maintainers |

This matrix is the honest boundary between a frozen contract and current
implementation. “Implemented” means a deterministic Host/schema path already
checks the invariant. “Partial” means only one adapter or a subset of the
fields is checked. “M3” means the law is frozen here but still needs a shared
conformance or install gate.

| Contract invariant | Authority | Current evidence | Status | M3 gate |
|---|---|---|---|---|
| Ownership and domain-neutral vocabulary | Constitution §2, §3.9 | `src/assayer_platform/contract.py`; boundary map | Partial | Generic API lint forbids frontend-only fields |
| Manifest identity and API compatibility | Plugin Contract §2 | `plugin-manifest.schema.json`; `validate_plugin_manifest`; registry tests | Implemented | Package/install gate for external entry points |
| WorkItem uniqueness and packet closure | Plugin Contract §3 | `PlatformKernel` semantic validation; kernel tests | Implemented | Shared plugin conformance fixture |
| Required dimensions gate decisions | Plugin Contract §4 | `decision.py`, kernel gates, rule-assessment schema | Implemented | Apply identical gate to interactive controller |
| Recovery before commit | Constitution §3.6; Plugin Contract §4 | Host recovery and kernel checks | Partial | Provider-agnostic recovery conformance suite |
| Capability intersection and fail-closed absence | Provider Contract §2, §4 | Provider descriptor schema, plugin declarations, and runtime capability profile | Partial | Provider loading, selection, and negotiation tests |
| Immutable, source-bound Evidence | Constitution §3.4; Provider Contract §4 | `EvidenceRecord` and packet validation | Partial | Cross-run/source mutation and cache invalidation tests |
| Idempotent commit receipts | Constitution §3.8; Plugin Contract §4 | Kernel receipt replay/conflict checks; ledger store | Implemented | Restart/replay conformance across all adapters |
| Canonical result and derived reports | Canonical Result Contract §1 | Platform ledger and JSON summary publisher | Partial | Emit/validate `canonical-result.schema.json` from every adapter |
| Explicit unknown, failure, and unavailable telemetry | Constitution §3.1, §3.11; Provider Contract §4 | Operation states, observability schemas | Partial | Failure taxonomy and missing-telemetry conformance |
| Batching/caching preserve proof | Constitution §3.10; Plugin Contract §5 | ExecutionProfile and kernel batch/split metrics | Partial | Adversarial batch, cache, ordering, and split fixtures |
| Version freeze and historical readability | Constitution §5 | Manifest/API checks and Run metadata | Partial | Migration registry and historical replay tests |
| Release only after conformance | Constitution §6 | Documentation and existing unit suites | M3 | Packaging/install refuses non-conforming artifacts |

## M2 exit statement

The platform constitution and v1 contracts are frozen as documentation. The
existing frontend compatibility path and built-in non-browser plugins remain
valid implementations, but the “Partial” and “M3” rows are not claims of full
platform enforcement. Stage 3 is the next implementation gate.
