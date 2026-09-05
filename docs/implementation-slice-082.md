# Implementation Slice 082 — Human-Readable Evidence Coverage

Status: implemented

Platform observability JSON and Markdown now include evidence-graph coverage.
The Markdown view states the covered/total candidate count, pending count,
overall completeness, and a bounded list of pending IDs. Per-WorkItem details
remain available in the machine-readable JSON view.

Coverage is derived from immutable InvestigationPackets and persisted review
checkpoints. It is diagnostic/presentational data only and cannot alter Run
state or formal conclusions.
