# Platform--Plugin Boundary Contract v1

| Metadata | Value |
|---|---|
| Document version | 1.1.0 |
| Date | 2026-09-07 |
| Status | Adopted; public-surface import gate 1.1.0 implemented (see §5) |
| Authority | Derived from Platform Constitution v1 and Plugin Contract v1 |
| Scope | Module ownership, dependency direction, compatibility bridges, and governance gates |

## 1. Decision

Assayer uses one stable platform with independently registered domain plugins.
The platform owns execution, evidence integrity, lifecycle, persistence,
recovery, validation, observability, and portable result delivery. A plugin owns
domain semantics and contributes checks through the public plugin contract.

This contract governs source dependencies and runtime authority. It does not
require separate processes or repositories.

## 2. Terms

| Term | Meaning |
|---|---|
| Platform kernel | Domain-neutral Run, WorkItem, Evidence, Decision, Result, lifecycle, persistence, and conformance implementation |
| Shared capability | A controlled source or transformation provider reusable by more than one plugin |
| Plugin | An independently inspectable package that contributes domain Checks |
| Host adapter | A product/runtime adapter that binds a provider to a browser, file system, repository, API, or other source |
| Agent adapter | Skill and model orchestration that requests evidence and submits semantic proposals |
| Compatibility bridge | Temporary code that translates an older domain-shaped API into the generic platform contract |

## 3. Ownership Matrix

| Concern | Platform kernel | Shared capability | Plugin | Host/runtime adapter | Agent/Skill |
|---|---:|---:|---:|---:|---:|
| Run identity and lifecycle | Owns | Uses | Observes | Binds | Reports |
| WorkItem identity and coverage | Owns validation | Supplies source identity | Defines discovery meaning | Collects source facts | Selects semantic focus |
| Source access | Authorizes | Implements bounded access | Declares requirements | Supplies runtime binding | Requests through Host |
| Evidence identity and persistence | Owns | Produces bounded facts | Interprets facts | Collects and sanitizes | References only |
| Domain rules and dimensions | Must not own | Must not own | Owns | Must not own | Applies through semantic judgment |
| Semantic decision | Validates shape and proof | Must not decide | Defines allowed meaning | Must not decide | Proposes |
| Commit and canonical result | Owns | Must not write | Supplies optional domain extension | Supplies compatibility projection | Reads |
| Recovery and retry | Owns generic barriers | Declares failure policy | Declares safe execution profile | Implements source recovery | Follows required next step |
| Logs and performance | Owns common telemetry | Reports provider timing | Reports domain labels | Reports runtime timing | May expose turn/model timing |

## 4. Allowed Dependency Graph

New code must follow this direction:

```text
Agent/Skill adapter ───────> public platform contracts
Plugin ────────────────────> public platform contracts
Plugin ────────────────────> declared shared capabilities
Host/runtime adapter ─────> public platform contracts
Host/runtime adapter ─────> provider implementations
Platform kernel ───────────> platform contracts and internal kernel modules
Report adapter ────────────> canonical result and ledger read models
```

The following dependencies are forbidden for new code:

```text
Platform kernel ─X────────> a concrete domain plugin
Platform kernel ─X────────> browser or document implementation
Plugin ─────────X─────────> Host SQLite tables or Host private modules
Plugin ─────────X─────────> another plugin's implementation
Plugin ─────────X─────────> direct model SDK, MCP transport, or CLI process
Agent ──────────X─────────> ledger mutation or provider implementation
Report adapter ─X─────────> mutation of a Run or Decision
```

Imports of a public registration type are allowed. Imports of a concrete
implementation are not a substitute for registration.

## 5. Platform Public Surface

Plugins may depend only on the following stable surfaces:

- `assayer_platform.contract` entities and versioned enums;
- top-level `assayer_platform.AgentContractBundle` for immutable, versioned
  Agent-facing Check contracts;
- plugin registration, manifest, and conformance APIs;
- capability-provider registration and negotiated provider interfaces;
- evidence-claim and decision validation APIs;
- result and staged-delivery contracts;
- documented platform context and failure types.

Plugins must not import private modules solely because they contain a useful
helper. A helper becomes public only after it has a documented ownership,
version, and conformance test.

The concrete, enforceable form of this surface is the symbol-level whitelist
in `assayer_platform.public_surface` (`PUBLIC_SURFACE`, versioned by
`PUBLIC_SURFACE_VERSION`). It is converged from the reference plugin
`ass-spec` and is checked by `assayer-plugin-surface-check`
(`assayer_platform.surface_conformance`). A plugin may import only the
modules and symbols listed there; widening the surface requires updating the
whitelist and this contract together and adding a conformance fixture for the
newly public symbol.

## 6. Plugin Responsibilities and Limits

A plugin may:

- define scope and Check metadata;
- discover domain WorkItems from authorized capability facts;
- convert bounded source facts into InvestigationPackets;
- define semantic-review instructions and domain Findings;
- declare execution profiles, required capabilities, and invalidation signals;
- provide an optional namespaced `domainExtension` for the canonical result.

A plugin may not:

- write platform or Host persistence directly;
- create or overwrite Host Evidence IDs;
- bypass recovery, safety, or coverage gates;
- publish a terminal status independently;
- modify common canonical-result fields;
- invoke a browser, file, network, or subprocess directly when a declared
  capability provider exists;
- place domain-specific branches in the generic kernel.

## 7. Capability Extension Rule

When a plugin needs a capability that the platform does not provide, the first
choice is an independent capability provider, not a kernel change.

The provider must declare:

- identity and platform API compatibility;
- capabilities and evidence kinds;
- scope and authorization requirements;
- limits and failure policy;
- algorithm versions and conformance fixtures.

The platform kernel may change only when the missing behavior is a lifecycle,
integrity, security, or common result concern shared by at least two domains.
The plugin-specific interpretation of a new fact remains in the plugin.

## 8. Compatibility Bridge Rule

Compatibility bridges are allowed only when all conditions hold:

1. The bridge is explicitly named and documented as compatibility code;
2. The generic contract remains the authoritative source of truth;
3. The bridge cannot create a second terminal result or ledger;
4. The bridge preserves generic identity, evidence, and validity semantics;
5. A removal condition and conformance test are recorded.

The current frontend canonical-result adapter is therefore a permitted bridge,
not a target platform layer. Generic publication must work without importing
that adapter.

## 9. Result and Ledger Authority

The platform ledger is authoritative. The platform derives common fields in the
canonical result:

- status and conclusion validity;
- coverage and outcome counts;
- Findings, review items, failures, performance, and trace;
- artifact identity and ledger digest.

Plugins may contribute only a validated namespaced domain extension or a
derived human-readable summary. They cannot override common fields, hide
unverified scope, or turn an invalid Run into a valid result.

## 10. Versioning and Migration

Boundary changes follow the platform constitution:

- changing authority, persistence meaning, safety, evidence closure, or result
  validity requires a major contract version;
- adding optional capabilities or metadata is a minor version change;
- wording-only or implementation-only corrections are patch changes.

Migration order:

1. Add the generic contract and conformance test;
2. Keep the compatibility bridge and dual-read if required;
3. Migrate one plugin without changing semantic outcomes;
4. Make the generic path the default;
5. Remove the bridge only after clean-install, resume, and terminal-result
   acceptance passes.

No physical package move is allowed to remove a compatibility bridge before the
generic path is independently verified.

## 11. Governance Gates

The following gates are mandatory for new platform or plugin work:

| Gate | Required evidence |
|---|---|
| Ownership | The change names its owning layer and rejected alternative layers |
| Dependency | Import direction passes the boundary test |
| Authority | The change does not create a second source of truth |
| Capability | New source access is a provider with scope, limits, and failure policy |
| Result | Common result fields are derived and validated by the platform |
| Recovery | Retry, stale state, and unknown-result behavior are explicit |
| Observability | Run, WorkItem, operation, and failure ownership remain inspectable |
| Compatibility | Existing plugin and user journey behavior is preserved or versioned |

## 12. Explicit Existing Debt

The repository does not satisfy every rule in this contract yet. The following
couplings are recorded as migration debt, not as permitted design:

- built-in plugins are imported from `assayer_platform.builtin_plugins`;
- the Host frontend compatibility path imports frontend plugin types;
- the Spec runtime still resolves and reads document paths directly in some
  code paths instead of requesting a file/document capability;
- generic and domain schemas share a physical directory;
- browser-shaped Host persistence remains beside generic platform persistence.

These paths remain operational for compatibility. They are frozen: new domain
behavior must not increase their use, and each one requires a conformance test
and removal or isolation condition before it can be considered resolved.

## 13. First Enforcement Scope

The first implementation slice should enforce only the highest-risk rules:

1. platform modules cannot import concrete plugin semantics;
2. plugins cannot import `assayer_host` private modules or mutate Host stores;
3. generic result publication does not require frontend canonical code;
4. each schema and provider declares ownership;
5. a non-browser plugin completes through the generic path;
6. plugins cannot import `assayer_platform` modules or symbols outside the
   public surface whitelist (enforced by `assayer-plugin-surface-check`).

This slice does not move files, remove built-in plugins, redesign browser
runtime behavior, or add cross-document semantic analysis.

## 14. Acceptance Statement

The boundary is considered governed when a new plugin can be installed through
registration, request a declared capability, run through the generic lifecycle,
submit semantic decisions, and publish a canonical result without importing
frontend code, browser internals, Host persistence, or another plugin.
