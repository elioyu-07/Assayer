# Vertical Slice 033: Host Runtime Events and Observability Artifacts (C08.2)

C08.2 persists Operation accepted/ended times, monotonic origin, and duration, with restart migration. Host emits scan.started, Operation start/finish, gate failures, terminal, and integrity events with contiguous per-Scan sequence. complete_audit publishes runtime-events.jsonl and observability-manifest.json; event bytes determine the manifest digest.

Unavailable Agent, Assessment, lease, model, transport, and process signals are marked honestly rather than zero. Tests cover real time, closure, digest consistency, privacy, and old SQLite migration.
