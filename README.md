# Assayer

**A capability platform for evidence-backed audits: intent in, verified artifacts out.**

Assayer is a domain-neutral audit platform for Codex. You give it an audit intent
and a business target; the platform compiles one typed, versioned Plugin
Contract, freezes the source through a capability Provider, plans bounded
semantic review, validates Evidence, Coverage, and the Ledger, and installs one
exact verified artifact.

Assayer is not a collection of domain runtimes. It provides the platform
guarantees every audit domain needs; domain meaning lives in plugins.

Codex CLI is the primary client. Codex Desktop uses the same Skill, MCP, and Host
path as a compatibility client.

> Assayer is in active Alpha development. The compiled-contract migration is
> ratified and the browser runtime has been retired; platform packaging and
> operator acceptance are still being finished.

## How it works

```text
natural-language intent + business target
                   |
             Agent / Skill          clarify intent, semantic judgment
                   |
           Assayer platform         compile contract · plan review · validate
                   |                evidence · coverage · ledger · release
         /                   \
  ordinary plugin        capability provider
   declarations           frozen source access
```

The platform compiles one typed/versioned Plugin Contract, a Provider freezes the
source into immutable snapshots, the Agent reviews bounded batches, the Host
validates Evidence, Coverage, and the Ledger, and an exact verified artifact is
installed and released.

## Ownership

| Component | Owns |
|---|---|
| Platform and Host | identity, lifecycle, authorization, Provider selection, source freezing, review planning, Coverage, Evidence, Ledger, recovery, result mapping, persistence, installation, release |
| Ordinary plugin | natural-language domain requirements, checks, review guidance, and business cases — declaration data only, no Python |
| Capability Provider | source acquisition, immutable typed snapshots, element enumeration, anchors, limits, and source-failure facts |
| Agent and Skill | clarification, bounded semantic review, rationale, and user interaction |

Plugin and Provider contracts cannot bypass platform safety, evidence,
persistence, or release gates.

## Results you can reason about

Every compiled Run ends in one platform status:

| Status | Meaning |
|---|---|
| `completed` | The declared scope met its coverage requirements and the result is final. |
| `partial` | Some conclusions are final, but named parts of the declared scope remain uncovered. |
| `failed` | No final result is available; the Run carries diagnostics and a recovery action. |

Review is exhaustive: the Host plans atoms as element × Check × Dimension, and
each atom reaches exactly one terminal state:

| State | Meaning |
|---|---|
| `satisfied` | The evidence supports the Check for this element. |
| `violated` | The evidence demonstrates a violation. |
| `not_applicable` | The Check does not apply to this element. |
| `unknown` | A discriminating fact is still missing; it is never treated as a pass. |
| `blocked` | A named dependency or safety gate prevents a decision. |

Completion is derived from the durable Coverage Ledger, not from an Agent claim.
The platform derives and validates the terminal result from the immutable ledger
and binds it to the ledger digest. Markdown reports, screenshots, and diagnostics
are presentation artifacts — never competing sources of truth.

## Plugins and Providers

An ordinary plugin is a declaration package — `plugin.yaml`, `checks.yaml`,
`semantic-review.md`, and `cases/` — with no Python. The compiler turns it into a
single `compiled-plugin.json` containing the normalized contract, compatibility,
Provider requirements, review schema, guidance, validation, `contractDigest`,
acceptance cases, package metadata, and release descriptor.

The author workflow is one command:

```bash
assayer plugin verify
```

Capability Providers have an independent contract and equivalent gates:

```bash
assayer-provider-check my_provider.registration:registration
assayer-provider-package-check ./my-provider-release
assayer-provider-install-check ./my-provider-release
```

The repository ships a markdown Provider and no bundled browser runtime. New
Providers register through the `assayer.providers` entry point group.

## Develop Assayer

```bash
python3 -m venv .venv
uv pip install --python .venv/bin/python -e packages/assayer-plugin-sdk
uv pip install --python .venv/bin/python -e '.[test]'
```

Run the fast development gate:

```bash
.venv/bin/python scripts/run_tests.py fast
```

Run the full release profile, which exercises the optional MCP SDK and rejects
skipped tests:

```bash
.venv/bin/python scripts/run_tests.py full
```

The repository also enforces deterministic boundary and governance gates:

```bash
.venv/bin/python scripts/resilience_scan.py --root .
.venv/bin/python scripts/check_architecture_boundaries.py
.venv/bin/python scripts/check_document_boundaries.py
.venv/bin/python scripts/check_plugin_source_boundaries.py
.venv/bin/python scripts/check_design_confirmation.py --git-range HEAD
```

## Governance

`docs/platform-constitution-v2.md` is the highest technical authority, and
`docs/design-confirmation-contract-v1.md` requires a machine-checked design
confirmation before implementation. Every change to a public contract, a schema,
or release behavior needs an approved `design/changes/<id>.json` record.

## Repository map

```text
plugins/assayer/        Codex Plugin source: Skill, MCP launcher, runtime prep
plugins/frontend-audit/ Declaration-only sample ordinary plugin
src/assayer_platform/   Platform kernel, compiled contracts, ledger, release
src/assayer_host/       Host, MCP transport, persistence, and recovery
src/assayer_plugin_sdk/ Public plugin and Provider contracts
packages/               Shipped distributions: platform, SDK, markdown provider
schemas/                Platform-owned ledger, coverage, and result schemas
docs/                   Constitution, contracts, and historical records
tests/                  Deterministic, MCP, plugin, Provider, and conformance tests
scripts/                Test, release, resilience, and governance tooling
```

## Documentation

Current authority:

- [Platform Constitution v2](docs/platform-constitution-v2.md) — active platform authority
- [Design Confirmation Contract v1](docs/design-confirmation-contract-v1.md) — machine-checked preflight required before implementation
- [Plugin Contract v1](docs/plugin-contract-v1.md) — the single compiled ordinary-plugin boundary
- [Plugin Development Standard v1](docs/plugin-development-standard-v1.md) — declaration authoring, compilation, and release gates
- [Capability Provider Contract v1](docs/capability-provider-contract-v1.md) — the controlled runtime capability contract
- [CI Support Matrix v1](docs/ci-support-matrix-v1.md) — supported interpreters and CI policy
- [Operator Release Gate v1](docs/operator-release-gate-v1.md) — operator acceptance evidence

Older vertical, journey, and plan documents are retained for traceability and
carry a superseded banner; they are not current authority. When documents
conflict, follow the authority order in the Platform Constitution.
