# Implementation Slice 081 — Evidence-Graph Progress

Status: implemented

Interactive progress now includes an additive `evidenceGraph` block with total
candidate count, covered count, pending candidate IDs, overall completeness,
and per-WorkItem coverage.

Coverage is calculated from immutable packet candidate IDs and durable review
checkpoints. The progress view does not infer semantic correctness; it only
answers whether every candidate has crossed a review boundary. Existing
workflow and terminal gates remain authoritative for finalization.
