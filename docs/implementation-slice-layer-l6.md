# Layer Convergence L6 — Legacy Coupling Gate

Status: implemented

The architecture checker now rejects plugin-bundle imports of platform-owned
ledger, canonical-result, reporting, observability, interactive, and result
delivery internals. Plugins must use the layer interfaces and platform entry
points instead of owning lifecycle or publication behavior.

This slice is a guardrail rather than a destructive cleanup: no historical
runtime path was removed. Existing Spec and Config implementations pass the
gate, and future boundary regressions fail in tests before packaging.
