# Assayer Platform Constitution v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-03 |
| Status | Frozen for M2; enforcement tracked by M3 |
| Owner | Assayer maintainers |
| Applies to | Platform kernel, plugins, capability providers, Agent adapters, and report adapters |

## 1. Purpose and authority

This Constitution is the highest technical authority for the domain-neutral
Assayer platform. It freezes the laws that every plugin and capability provider
must obey. The product contract still owns user promise and Alpha scope; the
domain rule contract owns the meaning of an individual check. When a lower
document conflicts with this Constitution, the lower document must be updated
before implementation continues.

The Constitution is intentionally small. It defines trust boundaries, result
integrity, lifecycle, compatibility, and release laws. It does not define how
a domain discovers facts or what a domain considers compliant.

## 2. Ownership boundaries

| Layer | Owns | Must not own |
|---|---|---|
| Platform kernel | Run identity and lifecycle, authorization, safety gates, WorkItem identity, Evidence references, persistence, recovery barriers, common decisions, observability, budgets, and publication | Domain correctness or a rule-specific branch |
| Audit plugin | Domain scope, checks, applicability, WorkItem discovery, investigation facts, required dimensions, semantic-review instructions, and domain summary data | Bypassing kernel gates, writing the ledger directly, or declaring a final result without a Host commit |
| Capability provider | Controlled access to an approved source (browser, file, repository, API, database, or log), capability metadata, limits, and source facts | Compliance decisions, rule changes, arbitrary writes, or widening user authorization |
| Agent/Skill adapter | Natural-language interaction, semantic investigation, decision proposals, and explanations | Inventing facts, changing protocol fields, bypassing safety, or publishing an unvalidated result |
| Report adapter | Deterministic views derived from the canonical result/ledger | Mutating conclusions, hiding invalidation, or becoming a second source of truth |

The user supplies intent and business input. The platform supplies protocol
versions, Run IDs, output locations, rule snapshots, and internal defaults.

## 3. Non-negotiable laws

1. **Fail closed.** Unknown, missing, stale, ambiguous, contaminated, or
   unverifiable evidence cannot produce a pass or a valid issue.
2. **Host is the trust boundary.** Only the Host may authorize capabilities,
   execute provider operations, bind identities, persist Evidence, and commit a
   formal Decision. The Agent may propose but never attest to a fact.
3. **One source of truth.** The immutable platform ledger is authoritative;
   summaries, JSON views, Markdown, and other artifacts are read-only
   derivatives.
4. **Evidence closure.** Every formal outcome references the current Run,
   WorkItem, Check version, Investigation Case, and valid Evidence. Evidence
   cannot cross identities, checks, or incompatible source states.
5. **Coverage is factual.** A plan, batch completion, candidate, or runtime
   success is not coverage. A pass requires every required dimension to have a
   current resolved Finding.
6. **Recovery precedes publication.** A Case with uncertain or failed recovery
   cannot support a formal Decision. Environmental contamination invalidates
   affected conclusions.
7. **Safety is deny-by-default.** A capability or action not explicitly
   authorized by the platform, provider, plugin, and user scope is rejected.
8. **Idempotency is mandatory.** A retry with the same identity and request
   digest reuses the known result. A changed request or unknown result fails
   closed and requires explicit resolution.
9. **Domain neutrality.** Generic contracts use WorkItem, Check, Evidence,
   Finding, Decision, and Capability terms. Browser concepts such as PageState,
   DOM, tab, and screenshot remain adapter or plugin details.
10. **Optimization preserves proof.** Batching, caching, parallelism, and
    compression are allowed only when the plugin declares them safe and the
    ledger retains per-item evidence and traceability.
11. **No silent degradation.** Missing Agent, provider, or transport runtime
    capabilities are reported as unavailable or failed; deterministic smoke
    cannot be presented as a production audit.
12. **Historical immutability.** A completed Run is interpreted with the
    frozen plugin, Check, capability, protocol, and algorithm versions recorded
    at its start.

## 4. Lifecycle law

```text
registered -> validated -> enabled -> selected -> running -> terminal
```

The Host freezes the selected manifest, Check, capability profile, scope
identity, and algorithm versions at Run start. A terminal Run is one of
`completed`, `partial`, or `failed`. A failed Run has no valid formal
conclusions; a partial Run retains only conclusions not invalidated by its
unfinished or contaminated scope.

A platform Run has no fixed wall-clock termination deadline. Long-running
audits may continue for hours while they make observable progress. Timeouts
are local controls for one provider or Host operation; they produce a classified
failure and recovery path, not an automatic termination of the whole Run.

## 5. Version and compatibility law

All platform, plugin, Check, capability, protocol, identity, evidence, and
result contracts use semantic versions.

- A **major** change alters lifecycle, safety, evidence, decision states,
  persistence meaning, or another invariant. It requires a new major API and a
  migration or explicit incompatibility error.
- A **minor** change adds backward-compatible optional fields, capabilities, or
  operations. Older consumers must continue to read the previous subset.
- A **patch** change clarifies wording or fixes an implementation defect without
  changing accepted data or behavior.

The Host rejects a plugin that requires an unsupported platform major or newer
minor API before discovery. Plugin and Check versions are independent. Rule
semantics are never silently reinterpreted for historical Runs.

## 6. Release law

An artifact may be published only when the canonical result validates, all
mandatory references close, and the plugin/provider conformance gates pass.
M2 freezes the contracts; M3 adds the machine-enforced install and release
gates. A plugin generated through Agent interaction is still an ordinary,
inspectable package and receives no trust exemption.
