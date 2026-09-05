# Four-Layer Platform Boundary Convergence

Status: implemented
Version: 1.0.0

## 1. Problem

The platform contracts now describe four logical layers, but some shared
behavior is still physically embedded in the Spec runtime and Host
compatibility adapters. In particular, Markdown parsing, source chunk
construction, candidate projection, and parts of review-envelope assembly are
not yet reusable through a stable platform interface.

This is an architectural convergence change, not a new domain feature. The
initial deployment remains one process and one repository.

## 2. Target ownership

### Runtime control

Owns Run and WorkItem lifecycle, batching, ownership, checkpoint persistence,
recovery, retry/split policy, concurrency limits, and terminal transitions.
It must not import Spec, frontend, Markdown, or browser semantics.

### Evidence and navigation

Owns source adapters, parsed navigation units, source chunks, stable source
identity, paging, evidence references, conservative grouping, and candidate
coverage indexes. It returns facts and locations, never domain findings.

### Review protocol and result

Owns bounded Agent review tasks, generic candidate disposition, evidence
closure, checkpoint coverage, Finding/Decision shape, canonical result, and
closeout. It validates structure and traceability, never domain severity or
meaning.

### Delivery and observability

Owns summary-first presentation, result paging, human-readable diary logs,
performance and failure diagnostics, artifact publication, and replay-safe
delivery.

### Plugins

Own only domain checks, terminology, candidate semantics, applicability,
severity, remediation guidance, and optional domain result extensions. A
plugin may declare required platform capabilities but may not reimplement
platform lifecycle, evidence paging, checkpoint storage, or closeout.

## 3. Migration decisions

1. Keep current public payloads backward compatible during migration.
2. Introduce platform interfaces first; migrate one plugin at a time.
3. Move Markdown parsing and source-unit normalization behind a generic
   `DocumentNavigationProvider`; the Spec policy remains in the plugin.
4. Move candidate collection and graph projection behind a generic evidence
   adapter; the plugin supplies only domain candidate extraction rules.
5. Keep Spec review validation in the plugin until a generic review contract
   can represent its checklist semantics without weakening current gates.
6. Keep Host/browser compatibility adapters outside the generic platform
   kernel; they may implement platform capability providers.
7. No process split, daemon, scheduler, or database migration is part of this
   change.

## 4. Implementation slices

### L1 — Boundary inventory and interface skeleton

Add import-direction checks and minimal interfaces for navigation, evidence
collection, review submission, and delivery. Exit: no new domain imports in
platform interfaces and tests prove dependency direction.

### L2 — Generic document navigation provider

Extract Markdown parsing output (`units`, source locations, source digest,
navigation identity) into a platform capability provider. Spec becomes an
adapter that maps provider facts to Spec candidates. Exit: Spec output remains
byte-for-byte compatible for existing fixtures and Config remains unaffected.

### L3 — Generic evidence collection and coverage index

Move candidate identity, paging, coverage accounting, and conservative graph
projection behind the platform evidence interface. Exit: Spec and Config can
publish the same graph contract without importing each other.

### L4 — Generic review task envelope

Expose a domain-neutral task envelope and compact decision submission API.
Retain Spec checklist validation as a plugin validator. Exit: a second plugin
can use the envelope without copying Spec fields.

### L5 — Delivery/observability adapter

Make terminal summary, canonical result, logs, and performance diagnostics
consume only platform result objects. Exit: plugins no longer write platform
artifacts directly.

### L6 — Legacy removal gate

After two plugin migrations and compatibility acceptance, remove duplicate
platform-like helpers from Spec and Host adapters. Exit: architecture checker
reports no known boundary violations and all golden journeys pass.

## 5. Invariants

- Domain semantics never move into the evidence or runtime-control layers.
- Platform layers never import a concrete plugin implementation.
- Every candidate retains stable identity and final disposition.
- Paging and checkpoint boundaries are durable and replayable.
- A plugin cannot publish a formal result without platform result validation.
- Existing terminal result and ledger identities remain stable during migration.

## 6. Acceptance

- Static dependency-direction checks pass.
- Spec and Config produce valid, equivalent generic evidence-graph shapes.
- Existing Spec fixture and interactive tests remain green.
- Result replay produces identical summaries before and after transport restart.
- No HTML or browser behavior is introduced by the migration.
- A real CLI acceptance remains a later user-run gate, not a substitute for
  deterministic contract tests.

## 7. Deferred

- Splitting the four layers into services or processes.
- Cross-document semantic analysis.
- Deep recursive crawling and broad new FUA checks.
- Marketplace and distributed plugin execution.
- Screenshot redaction and source-version proof.
