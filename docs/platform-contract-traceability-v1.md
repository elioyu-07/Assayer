# Platform v1 Contract Traceability

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-04 |
| Status | M2 baseline; M3 enforcement active |
| Owner | Assayer maintainers |

This matrix is the honest boundary between a frozen contract and current
implementation. “Implemented” means a deterministic Host/schema path already
checks the invariant. “Partial” means only one adapter or a subset of the
fields is checked. “M3” means the law is frozen here but still needs a shared
conformance or install gate.

| Contract invariant | Authority | Current evidence | Status | M3 gate |
|---|---|---|---|---|
| Ownership and domain-neutral vocabulary | Constitution §2, §3.9 | `src/assayer_platform/contract.py`; boundary map | Partial | Generic API lint forbids frontend-only fields |
| Manifest identity and API compatibility | Plugin Contract §2 | `plugin-manifest.schema.json`; registration conformance gate; registry tests | Implemented | Independent installer rejection for external distributions |
| WorkItem uniqueness and packet closure | Plugin Contract §3 | `PlatformKernel` semantic validation; kernel tests | Implemented | Shared plugin conformance fixture |
| Required dimensions gate decisions | Plugin Contract §4 | `decision.py`, kernel gates, rule-assessment schema | Implemented | Apply identical gate to interactive controller |
| Recovery before commit | Constitution §3.6; Plugin Contract §4 | Kernel packet barrier; interactive latest-recovery commit barrier; terminal result conformance; isolated release fixtures | Implemented | Add capability-specific recovery fixtures only when a Provider declares recovery |
| Capability intersection and fail-closed absence | Provider Contract §2, §4 | Provider descriptor schema; shared registration/runtime-shape conformance; duplicate-safe provider registry; four-way capability and limit negotiation; Host-created requests; provider-bound runner; blocked pre-construction gate | Implemented | Apply the shared path to external provider packages and product adapters |
| Immutable, source-bound Evidence | Constitution §3.4; Provider Contract §4 | Provider fact validation; Run/request/provider/capability/state/algorithm-bound `EvidenceRecord`; pre-decision Kernel enforcement | Implemented | Cross-process cache invalidation and historical replay tests |
| Idempotent commit receipts | Constitution §3.8; Plugin Contract §4 | Kernel receipt replay/conflict checks; ledger store | Implemented | Restart/replay conformance across all adapters |
| Canonical result and derived reports | Canonical Result Contract §1 | Generic batch, generic interactive, and legacy frontend Runs derive and schema-validate `canonical-result.json`; exact source-ledger digests are checked; staged and professional plugin views remain separate | Implemented | Prove historical migration fixtures when a source-ledger schema version changes |
| Explicit unknown, failure, and unavailable telemetry | Constitution §3.1, §3.11; Provider Contract §4 | Operation states, observability schemas, complete provider failure/retry conformance, safe runtime classification, no-blind-replay `result_unknown`, and the domain-neutral platform performance bill with Provider-bound timing | Implemented | Capture optional Agent/model/transport timing only from authoritative clients |
| Batching and parallelism preserve proof | Constitution §3.10; Plugin Contract §5 | ExecutionProfile; fail-closed parallel plan; negotiated worker ceiling; ordered merge; isolated failure and serial-equivalence tests; measured parallel window, summed task work, and estimate-only wait reduction | Implemented | Measure owner-run workloads without changing plugin declarations |
| Version freeze and historical readability | Constitution §5 | Manifest/API checks, Run metadata, launcher runtime reconciliation and identity marker, installation status, fail-closed plans, durable compensation, restricted Codex adapter, one-use externally authorized product plans, and transaction-gated private-runtime cleanup | Partial | Trusted Codex approval wiring, migration registry, and historical replay tests |
| Release only after conformance | Constitution §6 | plugin and provider registration, static package, and isolated install/fixture CLIs; conformance/release/fixture schemas; pre-wheel bundle gate | Implemented | Apply the gates to the first external Spec and Provider distributions |

## M2 exit statement

The platform constitution and v1 contracts are frozen as documentation. The
existing frontend compatibility path and built-in non-browser plugins remain
valid implementations, but the “Partial” rows are not claims of full platform
enforcement. Stage 3 is active: registration, pre-wheel interface, and static
package-resource and isolated execution checks are implemented. The first
external Spec distribution must now prove that the generic gate is sufficient
outside the platform source tree.
