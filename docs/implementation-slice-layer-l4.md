# Layer Convergence L4 — Generic Review Protocol Boundary

Status: implemented

The platform now provides a compact review-task envelope and a generic
submission validator. It enforces candidate identity coverage, one disposition
per candidate, and Evidence references limited to the task boundary.

The validator intentionally does not define domain severity, checklist
questions, semantic types, or what a disposition means. Existing Spec review
validation remains authoritative and can adopt this boundary incrementally.
