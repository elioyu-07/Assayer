# Spec-Quality Semantic Review Contract

The ass-spec plugin discovers and inspects Markdown specification
documents, then produces candidate findings for Agent review. The Agent is the
semantic decision boundary: the deterministic scanner only marks candidates and
never declares a formal pass or failure on its own.

## Decision boundary

For each candidate finding, the Agent must:

1. Confirm, suppress, or merge the candidate against immutable evidence (the
   source chunk, its line range, and the document digest) — never from memory.
2. For checklist dimensions, mark each as `PASS`, `FINDING`, `UNVERIFIED`, or
   `NOT_APPLICABLE` with a bounded explanation.
3. For cross-document relationships, resolve each declared relationship with a
   semantic type, status, severity, both document identities, and a resolution
   owner.

## Required evidence

- Candidate findings carry a `candidate_id`, `rule_id`, and source reference.
- Every decision must trace back to a source chunk and document identity.
- A `REWORK` finding requires a concrete next action.

## Non-goals

- HTML is not a canonical or required output. The plugin emits a structured
  review summary (candidate findings + checklist + cross-document review) and
  a portable platform result derived from the durable ledger.
