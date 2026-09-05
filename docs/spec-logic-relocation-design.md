# Spec-Quality Logic Relocation (Phase 2)

Status: implemented
Version: 1.0.0

## 1. Problem

The `spec_quality` plugin (`src/assayer_platform/builtin_plugins/spec_quality/`)
still contains generic, domain-neutral logic that any audit plugin needs:
source chunking, candidate indexing, candidate normalization, and generic
result-summary projection. Because this logic is private to the plugin, the
`config_quality` plugin re-implements its own simplified evidence-graph
projection instead of reusing the same capability.

This change moves those generic helpers into the platform layer interfaces so
every plugin reuses one implementation. The `spec_quality` plugin keeps only
its domain semantics (the 18 rules, terminology, severity, remediation
guidance, and Checklist semantic validation).

## 2. Scope

### Move to platform (generic, domain-neutral)

1. **Source chunking** — `_source_chunks`: split a document into bounded,
   stable, source-located reference chunks with deterministic IDs.
2. **Source reference locator** — `_source_ref_for_line`: map a line to its
   narrowest immutable source chunk reference.
3. **Candidate normalization** — `_candidate` / `_canonical_candidate` /
   `_navigation_candidates`: project a plugin candidate into the canonical
   `candidate_id`/`rule_id`/… shape with stable-ID dedup.
4. **Candidate index** — `_source_fact_index`: scan text for a plugin-supplied
   set of identifier/statement patterns and attach source references.
5. **Generic result summary** — `_actionable_result_delivery`: project
   confirmed decisions into the remediation + evidence-claim envelope.
6. **Stable digests** — `_digest` / `_document_state_digest`.

### Stay in the plugin (domain semantics)

The 18 rules (`checklist.json`), terminology/aliases (`_ALIASES`), the
ambiguity/conflict/missing-evidence definitions, severity vocabulary, remediation
wording, Checklist semantic validation (`review.py`), document-type
classification (`_document_context`), and cross-document relationships
(`_cross_document_*`).

## 3. Migration rules

1. Behavior is byte-for-byte identical for existing fixtures. Candidate IDs,
   source chunk IDs, source facts, evidence references, coverage counts, and
   canonical result must not change.
2. Platform code never imports a concrete plugin and never references
   `spec_quality` domain tokens (`FR-`, `CHK-`, REWORK, etc.). Domain tokens
   become parameters/config passed in by the plugin.
3. Each capability moves behind a platform entry point; the plugin delegates
   to it. The boundary checker (`scripts/check_architecture_boundaries.py`)
   must report no new violations.
4. No process split, daemon, or database change.

## 4. Target module placement

| Capability | Target platform module |
|---|---|
| Source chunking + source ref locator | `assayer_platform/source_chunking.py` |
| Candidate normalization | `assayer_platform/evidence_graph.py` (or `candidate_projection.py`) |
| Candidate index scanner | `assayer_platform/source_fact_index.py` |
| Generic result summary builder | `assayer_platform/actionable_result.py` (builder beside validator) |
| Stable digests | `assayer_platform/identity.py` |

## 5. Acceptance

- `spec_quality` and `config_quality` produce byte-identical output on existing
  fixtures before and after each migration step.
- Existing fast/full test suites remain green.
- `scripts/check_architecture_boundaries.py` reports no new violations.
- No `spec_quality` domain token appears in platform-layer modules.
