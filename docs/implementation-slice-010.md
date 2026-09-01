# Vertical Slice 010: Deterministic Report Derivation

Adds pure `DerivedReportBuilder`, which reads only schema-valid `audit-ledger.json`, never queries a browser, re-evaluates rules, or edits the ledger. It emits `issues.json` (valid `issue_found` only), `page-element-judgement.json` (all five states and references), `run-diagnostics.json`, Markdown summaries, and stable `audit.log` without request parameters or bodies. JSON uses sorted keys and stable indentation and records source-ledger SHA-256. Ledger and derived artifacts publish as one preflighted batch with conflict refusal and rollback. `complete_audit` returns relative artifact paths and never exposes absolute output roots.

No HTML is generated. Derived reports cannot invent issues, severity, Evidence, or conclusions and cannot mix review/noise/non-applicable/no-issue states into the primary issue list. Slice 011 adds the end-to-end Harness and runtime entrypoint.
