# Vertical Slice 009: Audit Convergence and Ledger Export

`complete_audit` validates login, registry digest, pending decisions, active Operations/Cases, and Coverage Universe closure. Every Entrypoint belongs exactly to processed, skipped, or unprocessed; duplicates, unknown references, and missing skip reasons reject. Host recomputes rule counts and coverage from formal Assessments. Processed Objects must have all potential rules decided; unfinished scope yields `partial`. Export includes Scan, frozen rules, PageStates, Entrypoints, Objects, Operations, Assessments, Cases, Evidence, Screenshots, and Issues. Schema validation, temporary file/fsync, atomic publication, `0600`, and conflict refusal protect artifacts.

No HTML is generated. Human Markdown/JSON views derive deterministically from `audit-ledger.json`; Slice 010 owns derivation. Full Harness and real-browser adapters remain later work.
