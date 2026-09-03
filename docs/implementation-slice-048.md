# Vertical Slice 048: Staged Terminal Result Delivery (M3)

Interactive plugin closeout now returns a bounded summary-first projection.
The platform recursively stages non-empty arrays and text larger than the
inline byte limit as stable result sections. This is domain-neutral: plugins
provide their semantic summary but do not implement cursors, pagination, or
chat-specific truncation.

`get_plugin_result` reads one requested section page. Its opaque cursor is
bound to the exact terminal result digest and section, every response is marked
`deltaOnly`, and a subsequent page never repeats earlier items. Nested arrays
are themselves sectioned, so one detail page cannot silently reintroduce an
unbounded child collection.

The first terminal response retains scalar status, readiness, coverage, and
count information while replacing detailed collections with section
references. The complete original terminal JSON is atomically written as
`result-summary.json`; canonical Evidence, Decisions, receipts, and failures
remain in the platform ledger. Staging changes delivery shape only and cannot
change audit conclusions, result validity, or coverage.

The normal product MCP surface exposes `get_plugin_result`; primitive lifecycle
tools remain diagnostic-only. Skills must show the overview first, fetch only
material detail sections, follow one cursor page at a time, and avoid repeating
previously presented sections.
