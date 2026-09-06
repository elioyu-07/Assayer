# Architecture Governance Baseline

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-05 |
| Status | Current-state baseline; implementation not started |
| Scope | Repository architecture and dependency ownership |
| Evidence | Source tree and import relationships in `src/` |

## 1. Purpose

This document records the architecture that exists in the repository today. It
is a factual baseline for the architecture-governance work. It does not change
runtime behavior and does not replace the normative platform constitution,
plugin contract, or canonical-result contract.

The governance question is simple:

> Can the platform remain domain-neutral while plugins own domain semantics and
> Agents provide semantic judgment?

## 2. Current Package Map

| Package | Current responsibility | Intended ownership |
|---|---|---|
| `src/assayer_platform` | Contracts, kernel, runner, registries, providers, result validation, reporting, interactive protocol, and bundled plugins | Platform kernel and shared contracts; bundled plugins are currently colocated and need a stricter packaging boundary |
| `src/assayer_host` | Browser runtime, Host lifecycle, persistence, transport, recovery, evidence, observability, and compatibility projections | Host/runtime adapters plus platform-facing transport; browser-specific behavior must not become generic platform behavior |
| `src/assayer_agent` | Model-independent Agent loop and turn orchestration | Agent/Skill adapter |
| `src/assayer_document_navigation` | Markdown parsing and provider registration | Shared capability provider |
| `rules/` | Frontend rule registry and FUA rule files | Frontend plugin-owned rule assets; legacy location remains coupled to Host |
| `schemas/` | Generic and browser/plugin persistence schemas mixed in one directory | Split by ownership while preserving compatibility paths |
| `plugins/assayer` | Installed Codex plugin and Skill packaging | Agent/Skill distribution |

## 3. Existing Logical Layers

The code already contains most of the following logical layers:

1. **Execution kernel**: `PlatformKernel`, `PlatformRunner`, ledger stores,
   interactive sessions, and run contracts.
2. **Capability and provider layer**: provider descriptors, negotiation,
   bounded provider execution, and the Markdown navigation provider.
3. **Plugin contract layer**: plugin manifests, checks, execution profiles,
   registration, package conformance, and entry-point discovery.
4. **Decision and result layer**: decision validation, evidence claims,
   canonical results, staged delivery, and result conformance.
5. **Host runtime layer**: browser sessions, actions, evidence capture,
   recovery, SQLite persistence, transport, and product lifecycle.
6. **Agent layer**: turn orchestration and semantic decision requests.

These layers are logical only. They are currently deployed in one Python
distribution and several packages, which is acceptable for the current stage.
The immediate goal is ownership clarity, not a service split.

## 4. Verified Dependency Direction

The following relationships are currently supported by source imports:

```text
Agent loop
    -> platform contracts

Document navigation provider
    -> public platform contracts

Platform kernel / runner
    -> platform contracts, registries, providers, ledger, result publication

Host transport
    -> platform runner and the built-in plugin registry

Host frontend compatibility projections
    -> frontend plugin types and browser-domain ledger

Spec plugin
    -> platform contracts and Markdown navigation provider
```

The first four relationships are compatible with the target architecture. The
last two are compatibility couplings that must be governed explicitly.

## 5. Current Ownership Findings

### GOV-001: Built-in plugins are physically inside the platform package

`src/assayer_platform/builtin_plugins/` contains the configuration, frontend,
and Spec plugins, and `builtin_plugin_registry()` is exported by the platform
package. This makes plugin code appear to be part of the platform kernel even
though the registry already supports external `assayer.plugins` entry points.

**Impact:** a plugin can be treated as an internal implementation detail; the
platform boundary is less credible than the registration contract suggests.

**Required direction:** keep compatibility registrations temporarily, but make
the external registration path the authoritative extension model. No new
domain behavior should be added to the kernel because a built-in plugin needs
it.

### GOV-002: Host directly depends on frontend-specific canonical output

`assayer_host/core.py`, `platform_store.py`, and `transport.py` import or branch
on `frontend_canonical_result` and frontend plugin identifiers. This is a
deliberate compatibility bridge, not a domain-neutral platform abstraction.

**Impact:** the Host product facade still knows that one plugin is the frontend
plugin; replacing or adding a different domain requires more than registration.

**Required direction:** generic canonical publication must be the default. A
frontend projection may remain as an explicit compatibility adapter selected by
plugin identity, but it must not be required by generic Run completion.

### GOV-003: Browser and generic concepts coexist in Host lifecycle types

`assayer_host` owns both browser-specific entities (`PageState`, entrypoints,
object identity, browser recovery) and generic platform persistence bridges.
The existing design documents already identify this as a migration boundary,
but the source package still exposes both responsibilities together.

**Impact:** future non-browser plugins may accidentally depend on browser-shaped
concepts, and browser assumptions can leak into generic lifecycle behavior.

**Required direction:** generic lifecycle contracts use Run, WorkItem, Evidence,
Decision, and Result concepts. Browser entities remain behind runtime adapters.

### GOV-004: Spec semantic implementation is correctly plugin-local, but its
protocol workload is still too high

The Spec runtime imports public platform contracts and the Markdown provider,
and keeps its policy, checklist, semantic review, and domain result projection
inside the plugin. That is the correct semantic ownership. However, the
current review path still constructs detailed Finding, dimension, reference,
and delivery structures that should eventually be derived by Host.

**Impact:** Agent and plugin code carry protocol assembly work that is common
to every domain and contributes to retries and slow closeout.

**Required direction:** retain Spec rules and semantic interpretation in the
plugin; move generic evidence-to-decision normalization and closeout mechanics
to the platform result layer.

### GOV-005: Generic and domain schemas share a physical schema namespace

`schemas/` contains generic run, evidence, provider, result, and observability
schemas together with browser and plugin-specific schemas. The schemas are
versioned and validated, but ownership is not visible from the directory
layout alone.

**Impact:** a domain schema can be mistaken for a platform contract, and public
schema changes become harder to review.

**Required direction:** classify schemas as `platform`, `capability`, or
`plugin`, then introduce ownership metadata and compatibility tests before any
physical move.

## 6. What Is Already Reusable Platform Capability

The following implementations are already substantially domain-neutral:

- `PlatformKernel` and `PlatformRunner`;
- `PluginRegistry` and plugin package conformance;
- provider registry, negotiation, and bounded provider execution;
- platform ledger and canonical result construction;
- evidence-claim validation;
- staged result delivery;
- parallel execution profile enforcement;
- platform performance accounting;
- Markdown navigation as a provider, not as a Spec rule engine.

These components should be extended through contracts, not copied into each
plugin.

## 7. What Must Remain Plugin-Owned

The following must not move into the generic kernel merely for convenience:

- the meaning of an `ass-spec` defect;
- the 18 `ass-spec` dimensions and their semantic thresholds;
- frontend FUA applicability and object semantics;
- domain severity and remediation language;
- cross-document Spec interpretation when that work is resumed;
- any rule-specific branch in the generic execution loop.

## 8. Governance Gaps to Close

The architecture is not yet fully governed until these checks exist:

| Gap | Exit condition |
|---|---|
| Plugin import isolation | A plugin-boundary test rejects platform imports of plugin semantics and Host imports of plugin implementation details except through registration/adapters |
| Generic publication path | Every plugin can produce a canonical result without a frontend-specific renderer |
| Generic lifecycle vocabulary | Non-browser plugins never need `PageState`, browser actions, or frontend ledger tables |
| Schema ownership | Every persistent schema declares `platform`, `capability`, or `plugin` ownership |
| Protocol burden | Agent submits compact semantic decisions; Host derives generic IDs, references, checklist projections, and closeout |
| Terminal reliability | A short deterministic plugin run always reaches a terminal state and writes the canonical summary |

## 9. Governance Order

The implementation order derived from this baseline is:

1. Freeze this current-state map and module ownership vocabulary;
2. Add boundary and import-direction conformance checks;
3. Separate generic publication from frontend compatibility publication;
4. Stabilize generic Run, WorkItem, Evidence, Decision, and Result lifecycle;
5. Reduce Agent protocol assembly through Host-derived result structures;
6. Migrate Spec to the generic path without changing its semantic rules;
7. Only then consider physically moving built-in plugins into independently
   packaged distributions.

Cross-document Spec semantics, new document formats, and frontend Alpha
acceptance are explicitly outside this governance slice.

## 10. Baseline Conclusion

Assayer already has a real platform kernel and extension contracts; this is not
a greenfield architecture. The current risk is boundary leakage, not absence
of abstractions. The highest-value governance work is therefore to make the
existing generic contracts authoritative, isolate compatibility code, and
reduce the amount of generic protocol work performed by Agent/plugin code.
