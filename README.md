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
in natural language ("install ass-spec", "what plugins do i have"); see
[the agent-first plugin lifecycle](docs/agent-first-plugin-lifecycle-design.md).

| Plugin | Status | What it does |
|---|---|---|
| `assayer.frontend-audit` | Usable Alpha | Audits anonymous test or staging web interfaces in Chromium with safe interactions and DOM/visual evidence; currently ships the FUA-10 filter-action check. |
| `ass-spec` | Usable Alpha | Reviews Markdown product and software specifications against the canonical 18-point quality policy with Agent semantic judgment. Lives in its own repository (`ass-spec`) and is installed as an external distribution. |

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
Audit this project's spec.md
```

That is the intended product boundary. Assayer owns run IDs, protocol versions,
revisions, output locations, browser profiles, checkpoint receipts, and recovery
state. The user supplies the audit intent and the target.

The current Alpha bundle is validated on macOS arm64 with CPython 3.13. Python
and Chromium are external prerequisites. The release is currently built from
source and installed through a personal Codex marketplace. Plugin installation,
upgrade, and removal are expressed as natural language in Codex and resolved by
the agent into a deterministic, fail-closed plan.

For the current delivery evidence, see [installation](docs/j01-install-delivery.md)
and [activation and discovery](docs/j02-activation-and-discovery.md). For a
step-by-step specification review walkthrough, see
[spec review getting started](docs/spec-review-getting-started.md).

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
| Platform | Lifecycle, permissions, evidence integrity, decision gates, persistence, recovery, observability, performance accounting, and canonical result semantics |
| Audit plugin | Versioned rules, applicability, WorkItem discovery, evidence organization, semantic-review requirements, and domain summary data |
| Capability provider | Authorized, bounded access to an environment or data source; never the compliance decision |
| Agent and Skill | User-intent resolution, orchestration, and semantic decisions requested by the plugin |

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
  durable checkpoints instead of being invalidated after an arbitrary duration.
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
| Contract enforcement | Registration, package, isolated-installation, result, recovery, provider, and performance conformance implemented |
| Recovery | Durable interruption recovery implemented and accepted in a real Codex CLI trial |
| Result model | Unified `canonical-result.json` implemented for every current plugin |
| End-to-end acceptance | J04/J05 implementation complete; fresh clean-CLI evidence and the real J08 lifecycle gate remain open |
| Plugin ecosystem | Built-in plugins today; independent persistent installation, upgrade, rollback, and uninstall remain in progress |

The ordered roadmap is:

1. close the remaining clean-CLI and release-lifecycle evidence for the current
   user journey;
2. complete measured platform-performance evidence;
3. externalize the ass-spec plugin without changing platform source (done: it now
   lives in its own repository and installs as an external distribution);
4. build an Agent-first plugin workbench backed by inspectable packages and
   mandatory conformance gates;
5. externalize frontend auditing and reusable capability providers;
6. expand the ecosystem only after two materially different external plugins
   prove the abstractions.

Deep crawler expansion, new FUA rules, marketplace UI, distributed execution,
automatic screenshot redaction, source-version attribution, persistent
cross-process caching, and plugin-specific micro-optimizations are intentionally
deferred. See the [project plan](docs/project-plan.md) for the authoritative
stage gates and sequencing.

## Develop Assayer

Create an environment with the MCP SDK, a locally installed Playwright driver,
and a Chromium-family browser (Google Chrome or Microsoft Edge).  Playwright and
the browser are user-supplied prerequisites, not bundled dependencies:

```bash
python3 -m venv .venv
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

The bundle contains the Skill, MCP launcher, version-aligned Python runtime,
rules, schemas, and plugin resources. The builder verifies offline installation
and launcher startup in a clean temporary environment.

## Build an audit plugin

An independently packaged audit plugin declares an `assayer.plugins` Python
entry point and ships its manifest, scope schema, runtime, semantic-review
instructions, and deterministic fixtures as one versioned identity.

Validate the registration during development:

```bash
assayer-plugin-check my_package.plugin:registration
```

Then validate the release without importing it, followed by an isolated local
installation and fixture run:

```bash
assayer-plugin-package-check ./my-plugin-release
assayer-plugin-install-check ./my-plugin-release
```

The publication gate must receive the exact wheel selected for release:

```bash
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist ./my-plugin-release
assayer-plugin-release-check --source ./my-plugin-release dist/*.whl
```

Strict interactive plugins additionally export one
`assayer.release_acceptance` entry point. The installed journey must cover all
declared checkpoint collections, finalization, resume, replay, terminal result
publication, and durable ledgers without Agent retries.

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
rules/                  Versioned frontend audit rules
schemas/                Protocol, ledger, result, provider, and diagnostic schemas
docs/                   Product, architecture, journey, and governance documents
tests/                  Deterministic, browser, MCP, plugin, provider, and conformance tests
scripts/                Test, release, resilience, and language-governance tooling
```

## Documentation

- [Project plan](docs/project-plan.md) — authoritative priorities, stage gates,
  progress, and deferrals
- [Platform foundation and user-journey plan](docs/platform-foundation-and-user-journey-plan.md)
  — the minimum shared capabilities required before expanding the plugin ecosystem
- [User journey and Definition of Done](docs/user-journey-and-definition-of-done.md)
  — the release acceptance boundary
- [Platform Constitution v1](docs/platform-constitution-v1.md) — frozen platform
  laws and ownership
- [Audit Plugin Contract v1](docs/plugin-contract-v1.md) — plugin behavior and
  lifecycle contract
- [Plugin Development Standard v1](docs/plugin-development-standard-v1.md) —
  executable Agent contracts, fail-fast validation, retry boundaries, and
  release gates
- [Capability Provider Contract v1](docs/capability-provider-contract-v1.md) —
  controlled runtime capability contract
- [Canonical Audit Result Contract v1](docs/canonical-result-contract-v1.md) —
  portable terminal result semantics
- [Plugin development](docs/plugin-development.md) — Python packaging,
  registration, and conformance workflow
- [Architecture](docs/architecture.md) — system structure and boundaries
- [Observability governance](docs/observability-governance.md) — logs, traces,
  diagnostics, and performance evidence

When documents conflict, follow the authority order in
[Design governance](docs/design-governance.md). Examples, tests, and generated
reports cannot override product contracts, user-journey gates, safety invariants,
protocols, or schemas.
