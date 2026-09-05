# Assayer Plugin Development Contract

The normative contract is [Audit Plugin Contract v1](plugin-contract-v1.md),
governed by the [Platform Constitution v1](platform-constitution-v1.md). This
document is the implementation guide for the current Python registration and
interactive lifecycle.

This document defines how an independently packaged plugin is discovered and
run by the Assayer platform.  A plugin owns domain facts and semantic rules;
the Platform Kernel owns lifecycle, safety, evidence closure, decision gates,
receipts, persistence, and observability.

## Registration

An installed Python distribution exports one entry point in the
`assayer.plugins` group.  The entry point resolves to either a
`PluginRegistration`, a zero-argument callable returning one, or an object
with a `registration` attribute containing one.

```toml
[project.entry-points."assayer.plugins"]
my_quality = "my_package.plugin:registration"
```

The registration must provide a validated `PluginManifest` and factories for
the plugin runtime and semantic decision provider.  A committer factory is
optional for in-memory or caller-owned durable commit paths.

Registrations should also publish a JSON-compatible `scope_schema`. The generic
`list_plugins` MCP tool exposes this schema before `run_plugin` is called, so
an Agent can discover required business inputs instead of guessing them.

Every manifest declares `platformApiVersion`. Assayer rejects a plugin that
requires another platform major version or a newer unsupported minor version
before discovery starts.

```python
from assayer_platform import PluginRegistration

registration = PluginRegistration(
    manifest=MyPlugin.manifest,
    plugin_factory=lambda runtime=None: MyPlugin(runtime),
    decision_provider_factory=lambda runtime=None: MyDecisionProvider(runtime),
    committer_factory=lambda runtime=None: MyCommitter(runtime),
    execution_modes=frozenset({"batch"}),
)
```

The registry rejects duplicate plugin IDs, ambiguous selections, malformed
entry points, and checks that are not declared by the selected manifest.
There is no implicit fallback to another plugin.

## Conformance before release

Validate a source-tree registration before building or publishing its package:

```text
assayer-plugin-check my_package.plugin:registration
```

The target may also be a zero-argument factory that returns a `PluginRegistry`.
The command emits a versioned JSON report and exits nonzero when the manifest,
runtime factory, batch decision provider, capability declaration, scope schema,
or safe execution profile is incomplete. Every issue names a stable `PCV1-*`
contract identifier and a required next action.

Registration discovery applies the same side-effect-free structural gate. The
release command additionally constructs the plugin and batch decision provider
and verifies their required interfaces. Construction must not contact a live
browser, repository, API, database, or other provider; live access begins only
after the platform starts an authorized Run.

The Assayer bundle builder applies this release gate to every bundled reference
plugin before creating wheels. This is the first package-time M3 gate. Package
content is checked separately before any plugin code is imported:

```text
assayer-plugin-package-check ./my-plugin-release
```

An independent package contains `assayer-plugin-release.json`. The descriptor
binds one plugin ID and version to its manifest, top-level scope schema, Python
runtime source, Markdown semantic-review contract, deterministic JSON fixtures,
and `pyproject.toml` entry point. All paths must remain inside the package and
must not traverse symbolic links. The static gate validates cross-file identity,
fixture Check references, and package metadata without executing plugin code.

Passing both checks proves static release completeness and constructable
interfaces. It does not yet install the distribution, execute its fixtures,
verify upgrade/rollback, sign it, or publish it. Those remain later M3/M4 gates.

The isolated release gate performs the remaining installation and fixture
checks without changing the user's environment:

```text
assayer-plugin-install-check ./my-plugin-release
```

It installs the local package into a temporary target with dependency download
disabled, launches a new Python worker, discovers exactly one matching
`assayer.plugins` entry point from that target, reruns registration conformance,
and reconciles runtime manifest and scope schema with the statically checked
files. It then runs every declared deterministic fixture through
`PlatformKernel` and compares exact terminal status and decision/failure
multisets.

Installation and fixture-worker budgets are release-test safeguards, not audit
Run timeouts. They are independently configurable with
`--install-timeout-seconds` and `--fixture-timeout-seconds`. The command does
not permanently install, upgrade, roll back, sign, or publish a plugin. It is
also process isolation for reproducibility, not an operating-system sandbox for
hostile code; only reviewed source packages should enter this developer gate.

## Runtime boundary

The plugin implements only domain operations:

```text
discover(scope, context) -> WorkItemSet
inspect(workItems, check, context) -> InvestigationPacketSet
```

The runtime adapter may use a browser, API, repository, file, database, or log
source.  It must not bypass Host safety or write directly to platform ledger
storage.

## Interactive plugin lifecycle

Plugins that need Agent-guided work declare `interactive` in their registration
and use the same domain-neutral lifecycle as every other interactive plugin:

```text
start_plugin_run -> advance_plugin_run
                  -> (semantic input + advance_plugin_run)*
                  -> formal summary
                  -> get_plugin_result(sectionId, cursor)* when detail is needed

resume_plugin_run(runId) -> advance_plugin_run* after Host interruption
```

The `advance_plugin_run` operation is the normal Host-driven product path. It
performs deterministic discovery and inspection, then pauses at an explicit
semantic boundary. After the Agent supplies a checkpoint or decision, the Host
continues paging, coverage validation, decision assembly, and eligible closeout
without requiring one tool call for each bookkeeping step. It also accepts an
explicit `partial` or `failed` closeout when a blocked Run cannot continue. The
normal product MCP catalog exposes `start_plugin_run`, `resume_plugin_run`,
`advance_plugin_run`, `expand_evidence_collection`, `get_plugin_result`,
`recover_work_item`, and `get_plugin_progress` for this lifecycle. Evidence
expansion is bounded and read-only; normal state changes still go exclusively
through `advance_plugin_run`. Resume uses the opaque Run ID retained from
start; Host startup never resumes implicitly. The lower-level
`discover_work_items`, `inspect_work_items`, `expand_investigation`,
`checkpoint_review`, `submit_decisions`, and `finish_plugin_run` operations
remain available for compatibility and diagnostics through the standalone
interactive transport; they are intentionally absent from the normal product
catalog.

Terminal delivery is summary-first for every plugin. The platform recursively
replaces non-empty arrays and oversized text in the Agent response with stable
section references. `get_plugin_result` returns only the requested next page
and an opaque cursor; it never repeats earlier pages. The original unabridged
terminal JSON is written to `result-summary.json`, while the ledger continues
to retain canonical Evidence and decisions. Plugins do not implement cursors,
chat-sized truncation, or result pagination themselves.

Every terminal response starts with a platform-owned `resultOverview`. It
states conclusion validity, coverage, outcome counts, review and failure
counts, and a plain-language next action. Decision detail pages retain the
committed decision reason and each dimension's status and reason.
`needs_review` decisions also produce platform-derived review items naming the
unresolved, blocked, or conflicting dimensions and the next evidence action.
A failed Run reports invalidated conclusions and does not expose earlier
decisions or optional plugin summary data as formal results; those records
remain available only in the canonical ledger for diagnosis.

Terminal publication stages `result-summary.pending.json` before the terminal
ledger transition and atomically promotes it to `result-summary.json` after
the ledger succeeds. The Host then publishes its latest-terminal pointer and
only afterward removes active resume metadata. These files are Host-owned
recovery state; plugins must not create, edit, or delete them.

The public payload contains only plugin/check identity, business scope, WorkItem
references, InvestigationPackets, and semantic DecisionProposals.  The platform
generates Run IDs, protocol versions, output paths, ledger entries, and commit
receipts.  A frontend plugin may continue to expose the historical
`start_audit`, `discover_scope`, and `investigate_object` names as compatibility
aliases, but new plugins must not copy browser-specific vocabulary into their
contract.

`inspect_work_items` is summary-first at the Agent-facing transport. A plugin
can declare large arrays through `InvestigationPacket.metadata.evidenceCollections`;
the platform then exposes stable group summaries and bounded collection pages.
Plugins must supply stable unique item IDs and may declare only mechanical
grouping fields. They must not implement their own transport cursor or treat a
mechanical group as a semantic decision.

Set `reviewRequired=false` for a collection that provides pageable reference
context but is not itself a queue of items that must receive durable semantic
review checkpoints. Reference collections never contribute to completion
coverage and cannot be checkpointed.

An interactive plugin may implement
`assemble_review_checkpoints(checkpoints, finalization, packet, check, context)`
to support incremental semantic review. The platform treats each checkpoint
payload as opaque plugin data, but validates its WorkItem, Check, declared
collection, stable item IDs, replay identity, and non-overlapping coverage.
Before assembly it requires the referenced checkpoints to cover every
non-empty `reviewRequired=true` collection exactly once. This lets a plugin
declare separate required queues, such as candidate dispositions and final
dimension reviews, without allowing either queue to be bypassed. The plugin
hook returns ordinary Decision `details`; existing decision and committer gates
remain authoritative.

The transport-independent reference implementation is
`InteractivePluginController`.  MCP and CLI adapters should delegate to it
instead of reimplementing lifecycle or validation rules.  A plugin may expose a
`restore(work_item_id, payload, context)` hook; when absent, the platform records
recovery as `not_required`.  Recovery status is diagnostic state and does not
replace the evidence and decision gates.

## Kernel entry point

Callers select a registered plugin by ID and Check.  Selection occurs before
discovery and failures are returned as a failed platform Run:

```python
result = PlatformKernel().run_registered(
    registry,
    scope,
    "CFG-001",
    context,
    plugin_id="my.quality-plugin",
)
```

The same kernel conformance gates apply to built-in and external plugins:

- WorkItem identity and duplicate handling;
- required evidence and dimension closure;
- capability negotiation and fail-closed behavior;
- declared batching, ordering, caching, and parallelism;
- recovery and checkpoint semantics;
- receipt-bound decision commits;
- terminal status, diagnostics, performance, and artifact publication.

Parallel inspection is opt-in. A plugin must declare both
`parallelism=allowed` and `ordering=independent`; the caller must also provide a
negotiated positive `maxConcurrency`. Otherwise the Kernel stays serial. Only
independent inspection is parallelized: semantic decision and commit remain
ordered, and packets are merged by original WorkItem order. Plugins must not
create their own unbounded executor. Persistent or cross-process platform caching
is not part of the current roadmap.

If a plugin commits through a domain system, its committer may delegate only a
narrow domain persistence callback. It must return a `CommitReceipt` with
`authority="platform"`; domain-side IDs belong in receipt metadata and never
replace the Platform `commit_id`. This keeps reports attributable to one
Platform decision while still allowing a browser, API, or repository ledger to
hold a compatibility projection.

## Product compatibility

The current frontend MCP names (`discover_scope`, `investigate_object`, and
`prepare_decision`) remain a compatibility surface for the first browser
plugin. They are routed through the registry and must not be copied into a
new plugin. The product MCP also exposes the domain-neutral `list_plugins` and
`run_plugin` entrypoints for registered batch plugins; they accept only
plugin/check identity and business scope while preserving the same ledger
invariants.

The `assayer.touchstone` plugin (now an independently installed distribution
rather than a built-in) is the first cross-domain interactive reference for
Markdown requirements. Its deterministic runtime owns only read-only Markdown
parsing and bounded candidate excerpts; an Agent must review that evidence and
submit the semantic decision. The platform still owns evidence closure, decision
gates, receipts, and publication. Scanner hits are therefore never promoted into
final Spec findings by the runtime alone.

## Capability provider registration

A reusable source adapter is registered separately from an audit plugin. Its
descriptor declares capabilities, scope, authorization, limits, failure
semantics, Evidence kinds, and algorithm versions. The Python distribution
exports a `ProviderRegistration` through `assayer.providers`; it does not add
provider code to the platform package or the plugin registry.

Before packaging, run:

```bash
assayer-provider-check my_provider.registration:registration
```

The command constructs the provider without calling its live `collect`
operation. Then validate the independent source package without importing its
code, followed by temporary installation and deterministic fixture execution:

```bash
assayer-provider-package-check ./my-provider-release
assayer-provider-install-check ./my-provider-release
```

The package root contains `assayer-provider-release.json`, a provider
descriptor, Python runtime source, `pyproject.toml`, and deterministic JSON
fixtures. Every declared capability needs a successful fact fixture, and the
release needs at least one classified-failure fixture. The isolated gate loads
exactly one `assayer.providers` entry point, reconciles the installed descriptor
with static package data, and runs fixtures through negotiated provider
requests. Release-test time budgets do not limit user audit Run duration.

These commands prove metadata, packaging, interface, and deterministic runtime
conformance. They do not access a production source, persistently install the
provider, or provide an operating-system sandbox for untrusted code. Real
adapter acceptance remains a distinct gate.

## Current migration boundary

The generic registry and batch Kernel path are implementation-ready. The first
browser plugin remains a compatibility adapter: its interactive MCP calls still
write a browser-shaped Host Assessment projection. The plugin translates that
atomic domain write into a distinct Platform receipt, and the interactive Run
persists it with `decision_authority=platform`. Receipt metadata identifies the
exact Host Assessment projection; the projection is not a second authority.
