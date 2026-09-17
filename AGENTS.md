# Assayer development rules

This repository is governed by [`docs/platform-constitution-v2.md`](docs/platform-constitution-v2.md),
[`docs/design-confirmation-contract-v1.md`](docs/design-confirmation-contract-v1.md),
and the subordinate contracts listed by the Constitution.

Before changing platform or plugin code:

1. Create `design/changes/<change-id>.json` and run
   `python3 scripts/check_design_confirmation.py` before editing implementation.
2. Do not implement until the record is machine-checked and, for any public,
   semantic, safety, persistence, or release change, human-approved.
3. Treat the Constitution as normative, not explanatory. Lower documents may
   only refine it and may not create exceptions.
4. Keep ordinary plugin author sources zero-Python and domain-only. Do not add
   platform schemas, registration, lifecycle, transport, Evidence, ledger,
   Provider, Agent, or compatibility code to `plugins/*`.
5. Run `scripts/check_architecture_boundaries.py`,
   `scripts/check_document_boundaries.py`,
   `scripts/check_plugin_source_boundaries.py`, the design-confirmation check,
   and focused tests before reporting completion.
6. A failed plugin verification invalidates any previous verified artifact; do
   not install or publish a stale wheel.
7. Do not restore deleted compatibility entry points merely to make historical
   tests pass. Update or remove tests for deleted production surfaces.

The repository architecture gate is authoritative for these boundaries. A
change that conflicts with it is incomplete even when unit tests pass.
