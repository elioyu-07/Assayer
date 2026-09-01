# Vertical Slice 011: Deterministic End-to-End Harness

Adds `run_deterministic_harness` through public `HostCore.handle`: bootstrap, page inspection, object verification, Case, safe action, Evidence, recovery, decision prepare/commit, and audit convergence. Adds `python -m assayer_host` and `assayer-harness`. Fixtures cover `scanned_no_issue` and `issue_found`, including Raw Visual, independent screenshot, and Issue traceability. CLI emits machine-readable JSON; output contains ledger, derived JSON/Markdown, logs, and screenshots, never HTML. End-to-end tests verify Operations, revisions, coverage, artifacts, issue chain, and fail-closed production adapters.

Harness uses static deterministic adapters for contract demos, regression, and CI only. It reads no real credentials or browser and cannot claim a real-site audit. Direct `HostCore()` remains fail-closed without explicit adapters. Real browser/MCP integration is later work.
