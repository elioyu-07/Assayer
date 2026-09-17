# Assayer

**Evidence-backed audits, governed from intent to result.**

Assayer is an audit platform for Codex. Give it an audit intent and a business
target—a web URL, a specification, or another supported source—and it coordinates
the evidence collection, semantic review, coverage accounting, recovery, and
publication needed to produce a traceable result.

Codex CLI is the primary client. Codex Desktop uses the same Skill, MCP, and Host
path as a compatibility client.

> Assayer is in active Alpha development. Two audit journeys are usable today;
> independent plugin distribution and the complete release lifecycle are still
> being finished.

## Why Assayer exists

An Agent can interpret a rule, but an audit needs more than a plausible answer.
It needs to show what was inspected, which evidence supports each decision, what
remains unknown, whether an interrupted run resumed safely, and why the final
conclusion is valid.

Assayer moves those responsibilities into a small, domain-neutral platform:

- users provide intent and business input, not protocol bookkeeping;
- plugins provide versioned domain rules, not private execution frameworks;
- capability providers expose controlled access to browsers, files, repositories,
  APIs, databases, or logs;
- the platform enforces evidence integrity, lifecycle, recovery, coverage, and
  result semantics across every audit domain;
- the Agent performs semantic judgment only where deterministic software cannot.

The goal is an ecosystem in which a new audit domain can be added without
rebuilding the trust model around it.

## Plugins

The current plugins are built-in implementations. They exercise the same generic
contracts intended for independently packaged plugins. Independently packaged
plugins are installed, upgraded, and removed through the agent-first lifecycle
in natural language (for example, "install example-plugin"); see
[the agent-first plugin lifecycle](docs/agent-first-plugin-lifecycle-design.md).

First-party domain plugins live under `plugins/` and use the same declaration
contract as external plugins. The platform does not assign a default domain;
the Host selects an installed plugin by its declared input scope.

A deterministic, non-browser configuration reference lives in `tests/helpers`
for exercising the generic kernel in tests; it is not a shipped plugin and never
appears in the plugin catalog.

Every plugin publishes the same portable `canonical-result.json`. A plugin may
also publish richer domain reports, but those reports cannot redefine the formal
result.

## Quick start

Once the Assayer Codex Plugin is installed and enabled, open a new Codex CLI task
and ask naturally:

```text
Audit http://localhost:8081/#/lease-mock
```

or:

```text
Audit this project's document.md
```

The equivalent explicit CLI entry accepts the same target kinds:

```bash
assayer audit https://example.test
assayer audit ./document.md
assayer audit ./policy.yaml --plugin policy-review
```

URLs route to the web-audit Skill. Files and directories route to the generic
installed-plugin workflow; the Host selects an installed plugin whose declared
scope matches the target and never starts Chromium for document-only inputs.

If the first run cannot start, use the read-only readiness check before
changing any configuration:

```bash
assayer doctor
assayer doctor --json
assayer doctor --fix
```

`doctor` is read-only by default. `doctor --fix` only prepares the bundled
private runtime; it does not start an audit or Chromium and does not install
domain plugins.

The check distinguishes required dependencies from target-specific ones (for
example, Chromium is required for a web audit but not for a Markdown review).
For a web target it checks for Playwright and a local Chrome/Edge executable,
but defers the actual browser launch until the audit starts. It never starts a
browser or an audit Run itself.

That is the intended product boundary. Assayer owns run IDs, protocol versions,
revisions, output locations, browser profiles, commit receipts, and recovery
state. The user supplies the audit intent and the target.

The current Alpha CI policy exercises CPython 3.11, 3.12, and 3.13: 3.11 and
3.13 run the full gate, while 3.12 runs the fast compatibility gate. The clean
Codex operator reference is macOS arm64 with CPython 3.13. Python and
Chromium are external prerequisites. Windows is not an officially supported
platform in this release policy. See the [CI Support Matrix](docs/ci-support-matrix-v1.md).
Clean Codex operator acceptance for J04/J05/J08 is governed separately by the
[Operator Release Gate](docs/operator-release-gate-v1.md); automated tests do
not substitute for that evidence.
The release is currently built from source and installed through a personal
Codex marketplace. Plugin installation, upgrade, and removal are expressed as
natural language in Codex and resolved by the agent into a deterministic,
fail-closed plan.

For the current delivery evidence, see [installation](docs/j01-install-delivery.md)
and [activation and discovery](docs/j02-activation-and-discovery.md). For
plugin authoring, see the [plugin development guide](docs/plugin-development.md).

## How it works

```text
natural-language intent + business target
                    |
              Agent / Skill
        routing and semantic judgment
                    |
             Assayer platform
      lifecycle · safety · evidence · recovery
       coverage · observability · publication
              /                 \
       audit plugin       capability provider
     rules and domain      controlled access to
      interpretation       browser/file/API/etc.
```

The boundaries are deliberate:

| Component | Owns |
|---|---|
| Platform | Lifecycle, permissions, source binding, execution planning, identity, Evidence lineage, incremental review and coverage, decision mapping, persistence, recovery, observability, result projection, packaging, and publication |
| Audit policy/plugin | One business version, input kind, rules, applicability, deterministic domain observations, semantic-review meaning, invariants, and business examples |
| Capability provider | Authorized source acquisition, immutable snapshots, anchors, and bounded source queries; never the compliance decision |
| Agent and Skill | User-intent resolution and semantic verdicts for the current Host-planned review batch |

Plugins and providers cannot bypass platform safety, evidence, persistence, or
publication gates.

## Results you can reason about

Every run ends in one platform status:

| Status | Meaning |
|---|---|
| `completed` | The declared scope met its coverage requirements and the conclusions are valid. |
| `partial` | Some conclusions are valid, but named parts of the declared scope remain uncovered. |
| `failed` | Formal conclusions are unavailable; the result contains diagnostics and a recovery action instead. |

Individual checks use five common decision states:

| Decision | Meaning |
|---|---|
| `issue_found` | The evidence demonstrates a rule violation. |
| `scanned_no_issue` | The required evidence closes the check without finding a violation. |
| `not_applicable` | The rule does not apply to the inspected subject. |
| `needs_review` | A named discriminating fact is still missing after documented safe checks. |
| `noise` | The candidate is not a valid audit subject or signal. |

`needs_review` is not a confidence escape hatch. It must identify the unresolved
fact, what was already attempted, and what could resolve the decision. Missing,
stale, ambiguous, or contaminated evidence is never converted into a pass.

Formal conclusions live in an immutable structured ledger. The platform derives
and validates `canonical-result.json` from that ledger and binds it to the exact
ledger digest. Markdown summaries, screenshots, diagnostics, and plugin-specific
views are presentation artifacts—not competing sources of truth.

## Reliability and observability

Long audits should be inspectable and resumable, not opaque.

- There is no fixed total audit timeout. Large scopes advance through bounded,
  durable lifecycle boundaries instead of being invalidated after an arbitrary
  duration.
- Interactive runs expose whether the Host is working, the Agent owes a semantic
  decision, recovery is required, or the run is terminal.
- Idempotent operations, revision fencing, and append-only corrections prevent a
  resumed Agent from duplicating or overwriting accepted work.
- Summary-first and paged responses keep Agent context bounded while complete
  evidence remains available in durable artifacts.
- Safe parallel inspection is available only when a plugin declares independent
  ordering and negotiated runtime policy permits it.

Each run records complementary views for different readers:

| View | Purpose |
|---|---|
| Canonical ledger | Immutable facts, evidence bindings, decisions, recovery, and terminal state |
| `canonical-result.json` | Portable, plugin-independent result for tools and downstream systems |
| Run diary | Human-readable chronology of phases, progress, waits, decisions, failures, and next actions |
| Diagnostics and performance bill | Failure ownership and time attribution across Agent, transport, Host, provider, browser, and scheduling |

Unavailable telemetry is reported as unavailable, never fabricated as zero. A
failed run never republishes earlier observations as formal conclusions.

## Project status

Assayer is deliberately separating implemented foundations from accepted product
journeys.

| Area | Current state |
|---|---|
| Frontend and Spec audits | Usable Alpha implementations |
| Platform contracts | v1 constitution, audit-plugin, capability-provider, and canonical-result contracts documented |
| Contract enforcement | Declaration, compiled-artifact, result, recovery, Provider, and performance conformance implemented |
| Recovery | Durable interruption recovery implemented and accepted in a real Codex CLI trial |
| Result model | Unified `canonical-result.json` implemented for every current plugin |
| End-to-end acceptance | J04/J05 implementation complete; deterministic CLI/lifecycle evidence is recorded, while real operator J04/J05 and J08 gates remain open |
| Plugin ecosystem | Independent SDK/platform/plugin/provider packaging is implemented; clean lifecycle evidence and the current hard-cut rollout remain in progress |

The ordered roadmap is:

1. finish the exact-contract hard-cut: reject old plugin/protocol paths and
   record clean-CLI and release-lifecycle evidence for the current journey;
2. complete measured platform-performance evidence;
3. externalize additional domain declarations without changing platform source;
4. build an Agent-first plugin workbench backed by inspectable declarations and
   mandatory conformance gates;
5. expand reusable capability Providers;
6. expand the ecosystem only after two materially different external plugins
   prove the abstractions.

Deep crawler expansion, marketplace UI, distributed execution,
automatic screenshot redaction, source-version attribution, persistent
cross-process caching, and plugin-specific micro-optimizations are intentionally
deferred. The historical [project plan](docs/project-plan.md) is retained for
traceability; current gates are defined by the Constitution and Design
Confirmation records.

## Develop Assayer

Create an environment with the MCP SDK, a locally installed Playwright driver,
and a Chromium-family browser (Google Chrome or Microsoft Edge).  Playwright and
the browser are user-supplied prerequisites, not bundled dependencies:

```bash
python3 -m venv .venv
# The root `assayer` distribution is platform-only. Ordinary plugins compile to
# data artifacts; this distribution ships no concrete capability Provider.
uv pip install --python .venv/bin/python -e packages/assayer-plugin-sdk
uv pip install --python .venv/bin/python -e packages/assayer-agent
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/python -m pip install playwright
```

Run the deterministic development gate:

```bash
.venv/bin/python scripts/run_tests.py fast
```

Run the full gate, which launches a local Chromium-family browser, exercises the
MCP SDK, and rejects skipped tests:

```bash
.venv/bin/python scripts/run_tests.py full
```

Build the current Codex Plugin bundle:

```bash
python3 scripts/build_plugin_bundle.py --output ./dist
```

The bundle contains the Skill, MCP launcher, version-aligned platform runtime,
schemas, compiled plugin resources, and Provider bindings. The builder verifies
offline installation and launcher startup in a clean temporary environment.

## Build an audit plugin

An ordinary plugin is a declaration package. It maintains domain metadata,
Checks, Dimensions, semantic instructions, and business cases. The platform
compiler generates one `compiled-plugin.json` containing the normalized
contract, Provider requirements, review plan, digest, and release identity.

The complete author workflow is one command:

```text
assayer plugin verify
```

It compiles the declarations, validates exhaustive review coverage, and verifies
the exact compiled artifact. Local source installation never copies a repository
into the plugin store.

The declaration compiler, CLI verification command, and Codex
`verify_plugin_source` MCP entry are implemented. There is no alternate
ordinary-plugin runtime path. See the [Plugin development guide](docs/plugin-development.md).

Capability providers have an independent contract and equivalent gates:

```bash
assayer-provider-check my_provider.registration:registration
assayer-provider-package-check ./my-provider-release
assayer-provider-install-check ./my-provider-release
```

These commands fail with stable contract identifiers and required next actions.
They do not permanently install the package or download its dependencies. Start
with the [plugin development guide](docs/plugin-development.md), then use the
normative [Audit Plugin Contract v1](docs/plugin-contract-v1.md).

## Standalone diagnostics

Inside Codex, prefer the natural-language workflow. The standalone commands are
useful for development and diagnosis:

```bash
# Start a separate Codex execution connected to a local Assayer MCP process.
assayer audit 'http://localhost:8081/#/lease-mock' --output-root ./assayer-output

# Collect Host facts only; this cannot publish an Agent-reviewed audit result.
assayer smoke 'http://localhost:8081/#/lease-mock' --output-dir ./assayer-smoke-output
```

`audit` never silently falls back to `smoke`. Low-level harness and transport
commands are development interfaces, not substitutes for a real user journey.

## Repository map

```text
plugins/                Codex Plugin source, Skills, launcher, and bundled resources
src/assayer_platform/   Domain-neutral contracts, kernel, registry, and test references
src/assayer_host/       Product Host, browser runtime, transport, persistence, and recovery
src/assayer_agent/      Model-independent Agent orchestration
src/assayer_plugin_sdk/ Public plugin/provider contracts and canonical SDK schemas
plugins/*/              Declaration packages and the bundled product Skill
schemas/                Platform-owned protocol, ledger, result, and diagnostic schemas
docs/                   Product, architecture, journey, and governance documents
tests/                  Deterministic, browser, MCP, plugin, provider, and conformance tests
scripts/                Test, release, resilience, and language-governance tooling
```

## Documentation

- [Project plan](docs/project-plan.md) — historical priorities and delivery evidence
- [Platform foundation and user-journey plan](docs/platform-foundation-and-user-journey-plan.md)
  — the minimum shared capabilities required before expanding the plugin ecosystem
- [User journey and Definition of Done](docs/user-journey-and-definition-of-done.md)
  — the release acceptance boundary
- [Platform Constitution v2](docs/platform-constitution-v2.md) — active platform
  authority, unified plugin boundary, exhaustive review, and development gate
- [Design Confirmation Contract v1](docs/design-confirmation-contract-v1.md) —
  machine-readable preflight required before implementation
- [Audit Plugin Contract v1](docs/plugin-contract-v1.md) — the single compiled
  ordinary-plugin boundary
- [Plugin Development Standard v1](docs/plugin-development-standard-v1.md) —
  author sources, compiler output, incremental review, and release gates
- [Simple Plugin Authoring Architecture](docs/simple-plugin-authoring-design.md)
  — target architecture, migration, and acceptance
- [Capability Provider Contract v1](docs/capability-provider-contract-v1.md) —
  controlled runtime capability contract
- [Canonical Audit Result Contract v1](docs/canonical-result-contract-v1.md) —
  portable terminal result semantics
- [Plugin development](docs/plugin-development.md) — declaration-only authoring guide
- [Architecture](docs/architecture.md) — system structure and boundaries
- [Observability governance](docs/observability-governance.md) — logs, traces,
  diagnostics, and performance evidence
- [Exact-contract, release, and performance evidence](docs/exact-contract-release-performance-evidence.md)
  — current hard-cut, lifecycle, and measured platform baseline

When documents conflict, follow the authority order in
[Design governance](docs/design-governance.md). Examples, tests, and generated
reports cannot override product contracts, user-journey gates, safety invariants,
protocols, or schemas.
