# Vertical Slice 039: Second-Use Run Isolation (J06a)

The product Facade and domain-neutral interactive transport now retire all
per-Run semantic state at terminal completion and before a new Run starts.
Durable ledgers and published artifacts remain available under the first Run's
output directory, while candidate mappings, investigations, pending-operation
state, and generic platform tracking cannot leak into the next Run.

The next Run receives a fresh Run identity, fresh output directory, fresh
platform tracking session, and zeroed public progress. It does not require a
plugin reinstall, MCP reconfiguration, or protocol input. Failure diagnosis
and retry policy remain a separate J06b backlog item.

Deterministic regression tests cover two consecutive frontend-compatible Scans
and two consecutive domain-neutral interactive plugin Runs. Real CLI reuse
acceptance remains part of the deferred J04/J07 owner-run evidence.
