# Platform v1 Contract Traceability

| Metadata | Value |
|---|---|
| Document version | 1.1.0 |
| Date | 2026-09-13 |
| Status | Superseded by Platform Constitution v2; retained as historical migration reference |
| Owner | Assayer maintainers |

> **Superseded.** This matrix records the former implementation baseline. It
> cannot authorize legacy paths or weaken the v2 Constitution.

This matrix is the honest boundary between a frozen contract and current
implementation. “Implemented” means a deterministic Host/schema path already
checks the invariant. “Partial” means only one adapter or a subset of the
fields is checked. “Not implemented” means the target authoring law is frozen
but still needs compiler, Host, conformance, or install work.

| Contract invariant | Authority | Current evidence | Status | Next gate |
|---|---|---|---|---|
| Domain-only ordinary author surface | Constitution §2, §3.14; Plugin Contract §2-4 | Target contract and design only; current top-level SDK still exposes low-level SPI | Not implemented | `assayer_plugin_sdk.simple` whitelist; Policy Pack and one-file minimal fixtures |
| Frozen typed source boundary | Constitution §3.16; Plugin Contract §5 | The source provider freezes typed snapshots and owns provider-bound WorkItems; legacy domain runtime adapters are no longer part of the ordinary path | Implemented for ordinary plugins | Add resume-time invalidation cases for each new source provider |
| Common incremental review and durable coverage | Constitution §3.17; Plugin Contract §6-7 | Current Host bounds Agent task input but accepts one complete WorkItem DomainResult | Not implemented | ReviewBatch/CoverageLedger persistence, replay, correction, and 10 MB/100,000-line acceptance |
| Single executable invariant declaration | Constitution §3.15; Plugin Contract §8 | DomainResult Schema, semantic rules, Markdown, runtime validators, and cases may still drift | Not implemented | Typed-model/invariant compiler equivalence and mutation tests |
| Generated ordinary release | Constitution §6; Plugin Contract §11 | `assayer plugin verify` and MCP `verify_plugin_source` compile Policy Pack/Simple sources, derive release and lifecycle artifacts, validate one exact wheel, and keep local installs wheel-only | Implemented for ordinary plugins | Migrate remaining Advanced SPI plugins without weakening their domain-result equivalence |
| Ownership and domain-neutral vocabulary | Constitution §2, §3.9 | `src/assayer_platform/contract.py`; boundary map | Partial | Generic API lint forbids source-specific fields |
| Generated manifest identity and API compatibility | Plugin Contract §2, §11 | `plugin-manifest.schema.json`; registration conformance gate; registry tests | Implemented for Advanced SPI | Compiler ownership and independent installer rejection for generated distributions |
| WorkItem uniqueness and packet closure | Constitution §2; Boundary Contract §6 | `PlatformKernel` semantic validation; kernel tests | Implemented internally | Remove WorkItem/packet construction from ordinary plugin surface |
| Required dimensions gate decisions | Plugin Contract §6-7 | `decision.py`, kernel gates, rule-assessment schema | Implemented for complete submissions | Apply the same gate incrementally from CoverageLedger |
| Recovery before commit | Constitution §3.6; Plugin Contract §4 | Kernel packet barrier; interactive latest-recovery commit barrier; terminal result conformance; isolated release fixtures | Implemented | Add capability-specific recovery fixtures only when a Provider declares recovery |
| Capability intersection and fail-closed absence | Provider Contract §2, §4 | Provider descriptor schema; shared registration/runtime-shape conformance; duplicate-safe provider registry; four-way capability and limit negotiation; Host-created requests; provider-bound runner; blocked pre-construction gate | Implemented | Apply the shared path to external provider packages and product adapters |
| Immutable, source-bound Evidence | Constitution §3.4; Provider Contract §4 | Provider fact validation; Run/request/provider/capability/state/algorithm-bound `EvidenceRecord`; pre-decision Kernel enforcement | Implemented | Cross-process cache invalidation and historical replay tests |
| Idempotent commit receipts | Constitution §3.8; Plugin Contract §9-10 | Kernel receipt replay/conflict checks; ledger store | Implemented | Restart/replay conformance across all adapters |
| Canonical result and derived reports | Canonical Result Contract §1 | Generic batch and interactive Runs derive and schema-validate `canonical-result.json`; compiler-generated ordinary plugins use common review and the platform report renderer; exact source-ledger digests are checked | Implemented | Prove historical migration fixtures when a source-ledger schema version changes |
| Explicit unknown, failure, and unavailable telemetry | Constitution §3.1, §3.11; Provider Contract §4 | Operation states, observability schemas, complete provider failure/retry conformance, safe runtime classification, no-blind-replay `result_unknown`, and the domain-neutral platform performance bill with Provider-bound timing | Implemented | Capture optional Agent/model/transport timing only from authoritative clients |
| Batching and parallelism preserve proof | Constitution §3.10, §3.17; Plugin Contract §7 | ExecutionProfile; fail-closed parallel plan; negotiated worker ceiling; ordered merge; isolated failure and serial-equivalence tests | Implemented for Advanced SPI | Move safe defaults and planning into Host without ordinary plugin declarations |
| Version freeze and historical readability | Constitution §5 | Manifest/API checks, Run metadata, launcher runtime reconciliation and identity marker, installation status, fail-closed plans, durable compensation, restricted Codex adapter, one-use externally authorized product plans, and transaction-gated private-runtime cleanup | Partial | Trusted Codex approval wiring, migration registry, and historical replay tests |
| Release only after conformance | Constitution §6 | plugin and provider registration, static package, and isolated install/fixture CLIs; conformance/release/fixture schemas; pre-wheel bundle gate | Implemented | Apply the gates to the first external plugin and provider distributions |

## M2 exit statement

The platform constitution and authoring contracts are frozen as target
documentation. The first migrated Policy Pack has completed its hard cut and
no longer has an Advanced SPI compatibility implementation. Remaining Partial
rows are platform work and do not authorize restoring an ordinary-plugin
fallback.
