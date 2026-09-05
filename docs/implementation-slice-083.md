# Implementation Slice 083 — Evidence-Graph Schema Gate

Status: implemented

The portable evidence-graph projection is now described by
`schemas/evidence-graph.schema.json` and covered by platform contract tests.
The schema constrains counts, candidate IDs, root-cause group members, and
per-WorkItem coverage fields without imposing any domain-specific semantics.

The existing runtime validator remains responsible for cross-field coverage
rules that JSON Schema cannot safely express. Both layers are required before
an adopting plugin can publish the projection.
