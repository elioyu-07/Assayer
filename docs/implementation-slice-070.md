# Vertical Slice 070: Parallel Inspection Contract

## Goal

Reduce platform waiting for large independent WorkItem sets without changing
scope, Evidence, decisions, failure semantics, or ledger reproducibility.

This slice deliberately does not build a persistent or cross-process platform
cache. The existing in-process compatibility cache remains unchanged and is
not part of the current performance roadmap.

## Safe planning

`ParallelExecutionPlanner` computes one closed execution plan from:

- the plugin's explicit `parallelism` declaration;
- the plugin's `independent` WorkItem ordering guarantee;
- the number of available inspection tasks; and
- the negotiated runtime `maxConcurrency` ceiling.

Parallel execution requires every condition. Missing concurrency budget,
plugin prohibition, one available worker, or insufficient work falls back to
serial execution. An inconsistent declaration that allows parallel work while
requiring non-independent ordering fails before inspection.

The actual worker count is:

```text
min(negotiated maxConcurrency, available independent inspection tasks)
```

No default worker count silently widens an absent runtime budget.

## Execution boundary

Only plugin inspection of independent WorkItems runs concurrently in v1.
Discovery, semantic decision, authoritative commit, recovery, and publication
remain ordered platform operations.

The Kernel starts bounded inspection tasks, waits without a total Run timeout,
validates every returned packet through the existing Evidence gates, and then
merges accepted packets in original WorkItem order. Completion race order
therefore cannot change Decision or ledger order.

A failed task is classified only against its own WorkItems. Other tasks finish
normally. A failed multi-item batch may split only when the existing plugin
contract independently allows safe failure splitting; parallelism does not
grant retry permission.

Provider-backed Runs inherit the negotiated Provider `maxConcurrency` through
`PlatformContext`, so the scheduler cannot exceed the Provider, platform, or
user ceiling established during capability negotiation.

## Observability

Every Run records `inspection.parallel.planned` with mode, reason, task count,
worker count, ordered merge, and failure isolation. Kernel metrics record:

- whether parallel execution was enabled;
- worker and task counts;
- parallel wall time;
- summed task duration; and
- an explicitly estimated wait reduction.

The human Run diary explains whether inspection was parallel or why it stayed
serial. Estimated wait reduction is diagnostic and never presented as exact
end-to-end user time saved.

## Acceptance evidence

Deterministic tests prove:

- worker count is clamped by negotiated runtime policy;
- missing policy falls back to serial;
- unsafe ordering fails before inspection;
- concurrent activity never exceeds the selected worker count;
- a Provider-backed Run cannot exceed its negotiated Provider ceiling;
- packet, Decision, and ledger order follow original WorkItem order;
- one failed task neither retries nor contaminates successful peers; and
- serial and parallel execution produce equivalent Evidence and decisions.

## Remaining boundary

This slice establishes the generic in-process inspection scheduler. It does
not enable parallel declarations for existing plugins automatically. Each
plugin or Provider must prove independence before changing its manifest, and
real performance claims still require owner-run workloads with captured timing
evidence.
