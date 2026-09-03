# Vertical Slice 041: Bounded Agent Waiting (M1 performance safeguard)

The model-independent AgentLoop now has a platform-wide context-size safety
limit for the serialized Host context presented to the Agent. A context that
cannot fit in one turn stops with an explicit `context_budget` reason and
preserves the last Host response for diagnosis. Total-run and per-model
durations are telemetry thresholds only; they are never task termination
deadlines. The default AgentLoop also has no fixed turn-count limit; explicit
test callers may still provide one to exercise bounded fixtures.

This is a user-experience safeguard, not the M3 throughput solution. A platform
Run has no fixed wall-clock timeout and may run for one or two hours while it
continues to make progress. Provider and Host operation timeouts remain local
failure controls; they cannot terminate the whole Run. M3 will add
paged/incremental operations, auditable grouping, layered packets, and adaptive
scheduling so normal large runs finish in bounded batches without requiring a
single oversized Agent context.
