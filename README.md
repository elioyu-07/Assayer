# Assayer

Assayer is an evidence-backed audit platform for Codex. A user describes what
to audit and provides a business input such as a Web URL or Markdown file.
Assayer selects a domain plugin, collects facts through controlled runtime
capabilities, and produces traceable audit decisions.

Codex CLI is the primary client. Codex Desktop follows the same Skill, MCP, and
Host path as a compatibility client.

## What works today

Assayer is an Alpha with two usable audit journeys:

- **Frontend audit:** inspect an anonymous test or staging URL with real
  Chromium, safe interactions, structured and visual evidence, recovery gates,
  and five-state decisions.
- **Spec-quality audit:** inspect a Markdown product or software specification,
  collect bounded candidate evidence, and let the Agent perform the required
  semantic review against the bundled Spec-quality policy.

The repository also contains a domain-neutral platform kernel, plugin registry,
interactive lifecycle, durable ledger, result schemas, conformance tests, and
configuration-quality reference plugin.

The plugin ecosystem is not complete yet. The frontend, Spec-quality, and
configuration-quality plugins are currently built-in reference implementations.
Independent plugin installation, upgrade, rollback, and uninstall are planned
work and must not be treated as delivered capabilities. See the
[project plan](docs/project-plan.md) for the ordered delivery gates.

## Use Assayer from Codex CLI

Install and enable the current Assayer Codex Plugin release, then open a new
Codex CLI task so its Skill and deferred local MCP server can be discovered.
Users do not configure MCP paths or provide protocol fields.

Ask naturally:

```text
Audit http://localhost:8081/#/lease-mock
```

or:

```text
Audit this project's spec.md
```

Assayer owns internal run IDs, protocol versions, output locations, revisions,
browser profiles, and commit receipts. The user supplies only intent and the
business target.

The current Alpha package is validated for macOS arm64 with CPython 3.13.
Python and Chromium are external prerequisites. The product MCP can report
read-only installation identity, version alignment, bundle status, and safe
feedback references without starting a browser. Stable upgrade, rollback,
uninstall, and cleanup remain part of J08; current package construction and
acceptance evidence are documented in
[J01 installation delivery](docs/j01-install-delivery.md) and
[J02 activation and discovery](docs/j02-activation-and-discovery.md).

## Understand the result

Every run ends in one platform status:

| Status | Meaning |
|---|---|
| `completed` | The declared scope met its coverage requirements and its decisions are valid. |
| `partial` | Some decisions are valid, but explicitly identified scope remains uncovered. |
| `failed` | The run cannot publish formal conclusions; diagnostics and a recovery step are provided. |

Each WorkItem and Check uses a common decision vocabulary:

- `issue_found`
- `scanned_no_issue`
- `not_applicable`
- `needs_review`
- `noise`

`needs_review` must identify the missing discriminating fact, the safe checks
already attempted, and the next condition that could resolve it. Unknown or
ambiguous evidence is never converted into a pass.

Generic plugin Runs return a platform-owned result overview before optional
domain detail. It explains conclusion validity, actual coverage, outcome
counts, review/failure counts, and the next action. Paged Decision details
retain the committed reason and dimension reasons. A failed Run never presents
its earlier Decisions or plugin summary as valid conclusions.

Canonical conclusions live in the immutable structured ledger. Markdown,
screenshots, diagnostics, and other presentations are derived artifacts and
cannot independently change an audit decision.

## How the platform is divided

```text
User intent and business input
              |
         Agent / Skill
  selection and semantic review
              |
       Assayer platform
 lifecycle, safety, evidence, decisions,
 persistence, recovery, diagnostics
          /             \
 Audit plugin       Capability provider
 rules and domain   browser, file, repository,
 interpretation     API, database, or log access
```

- **Platform:** owns lifecycle, permissions, evidence integrity, decision
  gates, persistence, recovery, observability, and common result semantics.
- **Audit plugin:** owns versioned domain rules, applicability, WorkItem
  discovery, evidence organization, semantic review requirements, and domain
  summary data.
- **Capability provider:** owns controlled access to an environment or data
  source and does not decide compliance.
- **Agent/Skill:** understands user intent, orchestrates the run, and makes
  semantic decisions where the plugin requires expert judgment.

Plugins and providers cannot bypass platform safety, evidence, persistence, or
publication gates.

## Current plugin interfaces

Registered plugins can be inspected from the developer CLI:

```bash
assayer plugins list --json
```

A registered batch Check can be run with explicit business scope:

```bash
assayer plugins run \
  --plugin assayer.config-quality \
  --check CFG-001 \
  --scope-json '{"files":[{"path":"settings.json"}]}'
```

Interactive plugins use the shared lifecycle:

```text
start_plugin_run
  -> advance_plugin_run
  -> advance_plugin_run (with each bounded semantic checkpoint)
  -> advance_plugin_run (with the final semantic decision)
  -> formal summary
  -> get_plugin_result (only for requested detail pages)
```

The Host owns deterministic discovery, inspection, paging, checkpoint
persistence, decision assembly, and eligible closeout. Recovery and progress
are available through `recover_work_item` and `get_plugin_progress`. Terminal
arrays and oversized text are exposed as result sections; the Agent reads only
needed pages while `result-summary.json` retains the complete output. Primitive
lifecycle tools remain available only through the standalone diagnostic
transport. Frontend MCP names such as `start_audit`, `discover_scope`, and
`investigate_object` remain compatibility aliases while the browser vertical
is migrated.

The frozen platform laws are documented in the
[Platform Constitution v1](docs/platform-constitution-v1.md). The current
Python packaging path and its limitations are documented in
[Plugin development](docs/plugin-development.md). Agent-assisted plugin creation
is planned after the M3 conformance and install gates are complete.

## Developer setup

Install the project with the real browser and MCP test dependencies:

```bash
python3 -m venv .venv
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/python -m playwright install chromium
```

Run the fast deterministic gate:

```bash
.venv/bin/python scripts/run_tests.py fast
```

Run the full gate, which launches Chromium, exercises the MCP SDK, and rejects
all skipped tests:

```bash
.venv/bin/python scripts/run_tests.py full
```

Build the current Codex Plugin bundle:

```bash
python3 scripts/build_plugin_bundle.py --output ./dist
```

The builder packages the Skill, MCP launcher, Python runtime dependencies,
rules, schemas, and plugin resources into one version-aligned archive. It
verifies the offline runtime and launcher in a clean temporary environment.

Validate one or more audit-plugin registrations before packaging them:

```bash
assayer-plugin-check my_package.plugin:registration
```

The JSON result identifies each violated `PCV1-*` contract and its required
next action. A failed report exits nonzero and must block publication.

Before installing an independent plugin distribution, validate its package
resources without importing its Python code:

```bash
assayer-plugin-package-check ./my-plugin-release
```

The package root must contain `assayer-plugin-release.json`, which binds the
manifest, business-input schema, runtime source, semantic-review instructions,
deterministic fixtures, and Python entry point to one plugin identity.

Run the isolated installation and deterministic fixture gate before release:

```bash
assayer-plugin-install-check ./my-plugin-release
```

This installs only into a temporary target, performs no dependency download,
loads exactly one declared entry point in a new Python process, reconciles the
runtime registration with the static package, and executes the declared
fixtures through the platform kernel. The temporary installation is deleted
after validation; this command does not promote the plugin into a user setup.

Capability providers use an independent entry-point and release gate:

```bash
assayer-provider-check my_provider.registration:registration
assayer-provider-package-check ./my-provider-release
assayer-provider-install-check ./my-provider-release
```

The static provider gate never imports runtime code. The isolated gate requires
exactly one `assayer.providers` entry point and executes deterministic success
and classified-failure fixtures through the negotiated Provider boundary.

## Standalone and diagnostic tools

Inside an existing Codex task, prefer the natural-language workflow above. For
terminal-only compatibility, `assayer audit` starts a separate Codex execution
and connects it to a local Assayer MCP process:

```bash
assayer audit 'http://localhost:8081/#/lease-mock' \
  --output-root ./assayer-output
```

Run a non-publishable Host fact diagnostic without Agent semantics:

```bash
assayer smoke 'http://localhost:8081/#/lease-mock' \
  --output-dir ./assayer-smoke-output
```

`audit` never silently falls back to `smoke`. The deterministic harness and
low-level transports are development interfaces, not user-facing proof that a
real audit journey works:

```bash
assayer-harness --output-dir ./audit-output
assayer-json --stdio --output-root ./assayer-output < requests.jsonl
assayer-mcp --output-root ./assayer-output
```

## Observability

Assayer records four related views:

- an immutable ledger containing formal audit facts and conclusions;
- a runtime event stream showing what happened and when;
- a public Agent decision trace explaining objectives and actions without
  exposing hidden reasoning;
- derived diagnostics and a performance bill for attribution and optimization.
- a portable `canonical-result.json` shared by every audit plugin and bound to
  the exact persisted source-ledger digest.

Diagnostics distinguish environment, capability provider, Host contract, Agent
strategy, rule contract, transport runtime, and target-application failures.
Model or transport telemetry that is unavailable is reported as unavailable,
never as zero.

## Delivery priorities

Development follows one active path:

```text
close the current J04-J08 user journey
  -> freeze the platform constitution and contracts
  -> enforce shared conformance and install gates
  -> externalize the Spec-quality plugin
  -> build the Agent-first plugin workbench
  -> externalize frontend auditing and capability providers
```

New FUA rules, marketplace UI, distributed execution, automatic screenshot
redaction, source-version proof, and unmeasured plugin-specific optimizations
are deferred while these foundations are completed.

## Repository layout

```text
plugins/                Codex Plugin delivery source and bundled Skills
src/assayer_platform/   Domain-neutral contracts, kernel, registry, and references
src/assayer_host/       Product Host, browser runtime, transport, and persistence
src/assayer_agent/      Model-independent Agent orchestration
rules/                  Versioned frontend audit rules
schemas/                Persistent, protocol, result, and diagnostic schemas
docs/                   Product, architecture, journey, and governance documents
tests/                  Deterministic, browser, MCP, plugin, and conformance tests
scripts/                Test, bundle, resilience, and language-governance tools
```

## Core documentation

- [Platform project plan](docs/project-plan.md)
- [User journey and Definition of Done](docs/user-journey-and-definition-of-done.md)
- [Platform Constitution v1](docs/platform-constitution-v1.md)
- [Audit Plugin Contract v1](docs/plugin-contract-v1.md)
- [Capability Provider Contract v1](docs/capability-provider-contract-v1.md)
- [Canonical Audit Result Contract v1](docs/canonical-result-contract-v1.md)
- [Platform v1 contract traceability](docs/platform-contract-traceability-v1.md)
- [Detailed platform contract reference](docs/platform-contract.md)
- [Plugin development contract](docs/plugin-development.md)
- [Architecture](docs/architecture.md)
- [Design governance](docs/design-governance.md)
- [Observability governance](docs/observability-governance.md)

When documents conflict, follow the authority order in
[Design governance](docs/design-governance.md). Examples, tests, and derived
reports cannot override the product contract, user-journey gates, safety
invariants, protocols, or schemas.
