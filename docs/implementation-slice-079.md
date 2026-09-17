# Implementation Slice 079 — Canonical Evidence-Graph Result

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


Status: implemented

`canonical-result.json` now has an optional `evidenceGraph` array. Each entry
is scoped to one WorkItem and carries candidate counts, pending member IDs,
coverage status, and conservative root-cause groups.

The platform validates the plugin projection before publishing it and applies
the same public-result schema to all adopting plugins. Existing result fields,
Finding semantics, and domain extensions are unchanged. Plugins that do not
declare or emit the feature simply produce an empty array.

The graph is additive traceability metadata. It cannot turn an incomplete Run
into a completed Run and it does not infer semantic conclusions.
