# Assayer Platform Constitution v2

| Metadata | Value |
|---|---|
| Document version | 2.0.0 |
| Date | 2026-09-15 |
| Status | Active and normative |
| Owner | Assayer maintainers |
| Replaces | Platform Constitution v1 and conflicting lower-level guidance |

## 1. Authority

This Constitution is the highest technical authority for Assayer. It governs
the platform kernel, Host, Agent orchestration, SDK, capability providers,
ordinary plugins, release tooling, and all repository development. A lower
document may add detail but MUST NOT weaken, reinterpret, or create an exception
to this Constitution. If documents conflict, implementation stops until the
higher document is corrected.

The Constitution defines platform ownership and trust boundaries. It does not
define the meaning of a particular business domain. Concrete plugin names,
rule families, Check IDs, input fixtures, and business workflows belong only
to plugin-owned material or explicitly non-normative historical records.

## 2. Vision and invariant

Assayer is a capability platform, not a collection of domain runtimes.

```text
User requirement in natural language
        |
        v
Agent structures and clarifies the requirement
        |
        v
Platform compiles one typed, versioned Plugin Contract
        |
        v
Platform-owned Provider freezes source capabilities
        |
        v
Platform-owned Agent reviews bounded batches
        |
        v
Host validates Evidence, Coverage, Ledger, and Result
        |
        v
Exact verified artifact is installed and published
```

The platform provides all general capabilities and all public interfaces.
An ordinary plugin provides only domain requirements: what should be checked,
what each outcome means, and the business examples that prove that meaning.
The platform absorbs lifecycle, source access, batching, evidence, safety,
recovery, persistence, result projection, installation, and release complexity.

## 3. Ownership boundary

| Owner | Owns | MUST NOT own |
|---|---|---|
| Platform kernel and Host | identity, lifecycle, authorization, Provider selection, source freezing, review planning, Coverage, Evidence, Ledger, recovery, result mapping, persistence, installation, release, and publication | a domain rule or a domain-specific branch |
| Agent orchestration | clarification, bounded semantic review, domain rationale, and user interaction | source access, platform identity, Evidence identity, coverage, persistence, tool authorization, or final publication |
| Capability Provider | source acquisition, immutable typed snapshots, element enumeration, anchors, limits, and source failure facts | business decisions, rule meaning, platform persistence, or expanded authorization |
| Ordinary plugin | natural-language domain requirements, Check/Dimension meaning, applicability semantics, outcome meaning, remediation, and business cases | Python, custom parsers, Provider access, transport, lifecycle, Evidence, Ledger, Result, custom prompts, custom schemas, or external effects |
| Report adapter | deterministic views of the canonical Result and Ledger | conclusions, ordering changes, hidden invalidation, or a second source of truth |

The number of internal Agents or services is an implementation detail. Their
ownership and trust boundary are fixed by this table.

## 4. One Plugin Contract

Every ordinary plugin has exactly one author surface:

```text
plugin.yaml
checks.yaml
semantic-review.md
cases/
```

Ordinary plugins contain no Python. They do not contain a runtime entry point,
Provider, parser, Agent, Skill, platform schema, registration, compatibility
matrix, lifecycle hook, commit function, result mapper, packaging script, or
release driver. A plugin may not add another author-maintained source of truth.

The declaration files express only:

1. plugin identity and one business version;
2. an input kind already supported by the platform;
3. stable Check, Rule, and Dimension identities;
4. applicability and the meaning of `satisfied`, `violated`,
   `not_applicable`, `unknown`, and `blocked`;
5. severity, remediation, and semantic review guidance; and
6. positive, negative, unknown, and not-applicable business cases.

The compiler derives every mechanical artifact: manifest, compatibility,
Provider requirements, review Schema, Agent guidance, validation, contract
digest, acceptance cases, package metadata, and release descriptor.

## 5. Natural-language authoring and compilation

Natural language is the authoring interface, not the runtime protocol. A
natural-language request MUST be clarified until the following are explicit:

- Check and Dimension meaning;
- applicability;
- satisfied and violated conditions;
- unknown and blocked conditions;
- Evidence requirements;
- severity and remediation;
- positive and negative examples.

The Agent MUST stop and ask a question when any of these is ambiguous. It MUST
NOT infer a rule from another plugin, historical behavior, or a hidden default.

The platform compiles the clarified request into one normalized, typed Plugin
Contract. That compiled contract is the only source used for installation,
execution, auditing, replay, and release. Its digest is frozen with the
business version and confirmation record.

The business owner MUST confirm the generated meaning and its examples before
the contract is frozen. A semantic change creates a new version and requires
new confirmation; an existing version cannot be silently rewritten.

Plugin natural language is untrusted domain data. It may describe meaning,
but it MUST NOT issue tool commands, change Agent roles, bypass safety, alter
the review protocol, request hidden context, or create an exception.

## 6. Platform-owned capability boundary

An ordinary plugin can select only an input kind already provided by the
platform. It cannot ship a parser, source adapter, browser client, file client,
network client, database client, or private Provider.

A Provider MUST:

- acquire and freeze the complete authorized source state;
- expose a versioned typed Snapshot and stable source anchors;
- enumerate the complete review universe for that input kind;
- expose bounded context navigation through platform interfaces;
- report truncation, permission, parse, and freshness failures explicitly; and
- bind every accepted Support to the frozen source state.

When a required capability is missing, plugin compilation or execution fails
closed and identifies platform capability work. The plugin cannot fill the gap
by adding a private interface.

## 7. Exhaustive review and Coverage

The platform, not the plugin, defines the review universe. For every supported
input kind, the Provider emits a complete, versioned element model. Each
element has a stable identity, parent/child relationships, source range,
bounded content projection, source revision, and parser version.

The Host plans all review atoms:

```text
element × Check × Dimension
```

Each atom MUST reach exactly one current terminal state:

```text
satisfied | violated | not_applicable | unknown | blocked
```

`not_applicable` may be omitted from a user-facing report, but it MUST remain
in the immutable Ledger with its applicability basis. An absent response is
never a pass. A missing, duplicate, foreign, stale, or malformed response is
rejected without mutation.

The Host delivers atoms in bounded ReviewBatches. The Agent must return a
decision for every atom in the current batch. It may request bounded context
through platform navigation, but it cannot read an unbounded source or bypass
the Provider.

The Host cannot publish a valid pass while any applicable atom is unresolved,
unknown, blocked, unprocessed, or contaminated. Completion is derived from
the durable CoverageLedger, not from an Agent claim.

## 8. Unified Agent result

Every plugin uses one platform-owned review result shape. A plugin cannot add a
second result protocol or redefine terminal states. Each atom result contains
the common fields required by the platform, including applicability, status,
rationale, Support handles, missing information, and remediation where needed.

Every terminal result is traceable:

- `satisfied` and `violated` require Support resolved by the Host;
- `unknown` records the missing or contradictory information;
- `blocked` records the owner and recovery boundary; and
- `not_applicable` records the applicability basis.

The Agent proposes. The Host resolves Support, validates membership and
invariants, persists the accepted batch, updates Coverage, and derives Finding,
Decision, canonical Result, and report projections.

## 9. Read-only ordinary plugins

Ordinary plugins are read-only. They cannot write a browser, file, repository,
database, API, or other external system. They cannot create an external
effect receipt or request elevated authorization.

If a future product requirement needs an external effect, the platform must
first add a public, versioned, safety-reviewed capability usable by every
eligible plugin. A private plugin escape hatch is prohibited.

## 10. Verification and release

No plugin is installed, enabled, listed as available, run, or published until
all gates pass:

1. source structure and declaration validation;
2. natural-language and semantic-contract validation;
3. public-interface and dependency validation;
4. generated-contract consistency and digest validation;
5. complete business, positive, negative, unknown, and not-applicable cases;
6. deterministic compiled-plugin artifact generation and inspection;
7. exact compiled-artifact installation in an isolated environment;
8. exhaustive ReviewBatch, Evidence, Coverage, replay, recovery, and result
   acceptance; and
9. exact compiled-artifact identity and release descriptor verification.

The source tree is never copied into the installation store. Ordinary plugins
are not Python distributions and never produce or consume a wheel. Provider
packages may use Python distribution tooling under the separate Provider
contract. A failed gate invalidates any prior verified artifact for the same
source revision.

## 11. Versioning and hard cut

Platform implementation changes that preserve the public contract are
transparent to plugins. A public compatible change requires plugin recompilation
and a new exact artifact. A semantic, lifecycle, or safety change requires
business reconfirmation and full re-acceptance.

Old plugin protocols, compatibility entry points, and domain-specific adapters
are not supported as ordinary fallbacks. Retired alternate plugin runtimes are
deleted rather than adapted.
Artifacts that do not satisfy this Constitution are disabled or removed.

## 12. Development preflight

Every platform, Provider, Agent, SDK, contract, and plugin change MUST begin
with a versioned machine-readable Design Confirmation. The record identifies
the change owner, authority documents, ownership split, required capabilities,
forbidden dependencies, public-contract impact, acceptance gates, and required
human approval.

The Agent cannot enter implementation until the preflight passes. CI repeats
the check before commit or merge. A design record cannot be edited silently
after implementation begins; a changed scope requires a new record and new
approval.

## 13. First capability and non-goal

The first source capability may be a structured document Provider. It is a
validation slice for exhaustive enumeration, bounded context, per-atom review,
and durable Coverage. It is not a built-in business domain and MUST NOT cause
source-specific branches in the platform kernel.

The goal is not to make one domain special. The goal is to prove that any
future plugin can supply requirements through the same contract and receive
the same platform guarantees.
