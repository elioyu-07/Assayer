# Vertical Slice 047: Host-Driven Interactive Workflow (M3)

Interactive plugin Runs now expose an explicit workflow boundary on every
generic response and in the durable platform ledger. The boundary includes the
current phase, lifecycle state, whether closeout is allowed, the required next
operation, and counts of WorkItems or review items that remain.

The Host-driven `advance_plugin_run` operation performs deterministic discovery
and inspection automatically. It pauses only when Agent semantic input is
required, returning a bounded semantic task. A supplied checkpoint is persisted
and the Host advances to the next task. A supplied decision is assembled and
committed through the existing evidence, decision, and receipt gates; when all
coverage is valid, the Host performs eligible closeout and returns the formal
plugin summary.

Runs with unresolved failures are reported as `blocked`; Runs waiting for a
semantic judgment are reported as `awaiting_agent_decision`; and fully decided
Runs are reported as `ready_to_finish` until closeout completes. A response or
Agent message that stops without one of these explicit boundaries cannot be
treated as a completed audit.

The existing lower-level interactive operations remain available for
compatibility and diagnostics through the standalone interactive transport.
They are hidden and rejected by the normal product MCP surface, which exposes
only start, Host-driven advance, recovery, and progress. Explicit partial or
failed closeout is supplied through `advance_plugin_run`, so hiding
`finish_plugin_run` does not strand a blocked Run. This slice does not invent
semantic decisions, truncate Evidence, add an elapsed-time cutoff, or change
the canonical result gates. Staged user-facing output and delta transport
remain the next context-governance slice.
