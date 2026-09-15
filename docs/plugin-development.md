# Assayer Plugin Development Guide

> **Authoring guide.** The Policy Pack/Simple SDK compiler and
> `assayer plugin verify` command are implemented. The current
> `PluginRegistration` and `DomainResultContract` flow remains an Advanced SPI
> migration path for legacy plugins; do not use it as the scaffold for a new
> ordinary plugin.

The normative contract is [Audit Plugin Contract v1](plugin-contract-v1.md),
governed by [Platform Constitution v1](platform-constitution-v1.md). The target
architecture and migration sequence are in
[Simple Plugin Authoring Architecture](simple-plugin-authoring-design.md).

## Choose the authoring level

Use the lowest level that can express the domain:

| Level | Use when | Author maintains |
|---|---|---|
| Policy Pack | Rules and recognizers are declarative | metadata, Checks, instructions, cases |
| Simple SDK | Deterministic domain scanning needs Python | the Policy Pack plus `scan` logic |
| Advanced SPI | A custom provider, external effect, non-standard workflow, or irreducible result model is required | reviewed low-level contract and its conformance suite |

Batching, caching, pagination, reporting, packaging, or a missing convenience
helper are not reasons to use Advanced SPI.

## Create an ordinary plugin

The ordinary source tree is:

```text
my-plugin/
├── plugin.yaml
├── checks.yaml
├── semantic-review.md
├── cases/
│   ├── pass.yaml
│   └── issue.yaml
└── plugin.py                 # optional
```

### Plugin declaration

`plugin.yaml` contains only business-owned metadata:

```yaml
id: dev.example.spec-quality
name: Spec quality
description: Reviews Markdown specifications for delivery risk.
version: 1.0.0
input: markdown
checks: checks.yaml
instructions: semantic-review.md
```

Do not add a platform API version, protocol range, SDK range, capability list,
execution mode, entry point, scope Schema, or release descriptor. The compiler
derives them from the input kind and compiler identity.

### Checks

`checks.yaml` owns domain meaning:

```yaml
checks:
  - id: PERM-001
    title: Permission denial behavior
    description: Permission requirements define roles, data scope, and denial responses.
    applicability: Documents that define protected operations or data.
    default_severity: P2
    recommendation: Define roles, data scope, and denial responses.
    unknown_when: The relevant section or referenced authority is unavailable.
    detect:
      contains_all: [permission]
      absent_all: [denied, unauthorized, data scope]
```

Check and rule IDs are stable domain identities. The plugin has one author-owned
business version; the compiler records the distinct generated Check and
contract identities needed by the Host.

`detect` is optional. It supports deterministic `contains_all`, `contains_any`,
and `absent_all` predicates over the frozen document. Without `detect`, the
Check still becomes a semantic review dimension. A Policy Pack containing only
these declarations, Markdown instructions, and cases needs no `plugin.py`.

### Business cases

Each Check must be covered by at least one `cases/*.yaml` file. Inputs are
resolved relative to that case file:

```yaml
input: weak-spec.md
expect:
  candidate_rules: [PERM-001]
  final: rework
```

`final` is one of `ready`, `rework`, `needs_review`, or `not_applicable`.
The compiler turns these expectations into installed lifecycle acceptance;
authors do not write start, resume, pagination, replay, or publication code.

### Optional deterministic scanning

Use `plugin.py` only when declarations cannot recognize the candidate:

```python
from assayer_plugin_sdk.simple import Candidate, Document, policy_plugin


@policy_plugin(
    id="dev.example.spec-quality",
    version="1.0.0",
    input="markdown",
    checks="checks.yaml",
    instructions="semantic-review.md",
)
class SpecQuality:
    def scan(self, document: Document):
        if document.contains("permission") and not document.contains_any(
            "denied", "unauthorized", "data scope",
        ):
            yield Candidate(
                rule="PERM-001",
                subject="permission behavior",
                message="Permission behavior lacks denial and data-scope rules.",
                support=document.absence(
                    ["denied", "unauthorized", "data scope"],
                    scope=document.full_scope,
                ),
                severity="P2",
                recommendation="Define roles, data scope, and denial responses.",
            )
```

The scanner receives a frozen typed document. It does not read a path, calculate
a digest, call a provider, create Evidence, or construct a platform result.

Only these ordinary concepts are public: `policy_plugin`, `Document`,
`Candidate`, `Fact`, `Relation`, `Support`, `Unknown`, and `invariant`.

### Semantic instructions

`semantic-review.md` explains domain judgment: what confirms or suppresses a
Candidate, what is unknown or not applicable, what evidence distinguishes the
outcomes, and how domain relationships affect the conclusion.

Do not describe JSON fields, platform IDs, cursors, checkpoints, digests,
recovery, replay, or finalization. The compiler generates the Agent result shape
from the common review model and typed invariants.

Run the complete gate with:

```bash
assayer plugin verify . --output-dir .assayer/verified
```

The command compiles into a temporary tree, builds one isolated wheel, installs
and exercises that exact wheel, and publishes it only after all gates pass.

Inside Codex, the equivalent developer entry is natural language: “verify this
local Assayer plugin source.” The `assayer-plugin-development` Skill calls the
Host-owned `verify_plugin_source` MCP tool with the absolute project directory.
The tool writes a passed artifact under `.assayer/verified` and returns its
plugin identity, exact path, SHA-256, and stage results. Codex does not recreate
the compiler, build, installation, or lifecycle checks and verification alone
does not install or publish the plugin.

### Business cases

Cases contain business inputs and expected domain meaning:

```yaml
input: fixtures/weak-spec.md
expect:
  candidate_rules: [PERM-001]
  final: rework
```

Do not call `start_plugin_run`, `advance_plugin_run`, `resume_plugin_run`, or
result paging tools in a case. The platform generates those lifecycle journeys.

## Evidence and large documents

Use only Supports returned by the frozen document API:

```python
document.lines(10, 14)
document.search("permission")
document.section("Authorization")
document.absence(["denied", "unauthorized"], scope=document.full_scope)
```

The Host resolves Support into canonical Evidence, builds lineage and Evidence
graphs, and rejects stale or out-of-scope references. `absence` returns Unknown
unless the provider can prove a complete closed search scope.

The Host divides large reviews into bounded batches and persists each accepted
batch. Plugin code and Agent output never maintain cursors, coverage state, or a
complete document result.

## Verify

Run the single deterministic gate:

```text
assayer plugin verify
```

The command will:

1. validate the domain declarations and Simple imports;
2. compile manifest, Schemas, registration, compatibility, and release data;
3. build an isolated wheel;
4. validate and install that exact wheel;
5. derive lifecycle tests from the business cases; and
6. verify Evidence, incremental coverage, resume, replay, correction,
   pagination, canonical result, and artifact integrity.

Local installation also consumes this wheel. It never copies `.git`, `.venv`,
tests, `build`, `dist`, or other repository content into the plugin store.

## What the platform owns

Ordinary plugins do not implement or maintain:

- WorkItems, InvestigationPackets, EvidenceRecords, Evidence Graphs, or IDs;
- manifests, JSON Schemas, semantic digests, registrations, entry points, or
  compatibility matrices;
- provider capabilities, authorization scopes, source freezing, or caching;
- Agent projection, batching, coverage, validation, or DomainResult mapping;
- committers, receipts, summaries, finalizers, pagination, recovery, or replay;
- release descriptors, acceptance drivers, catalog checksums, or installation
  store state; or
- MCP, Codex Skill, CLI, or other product transport names.

If an ordinary plugin appears to need one of these, treat it as a missing
platform/compiler capability. Move to Advanced SPI only when the admission
criteria in the boundary contract are actually met.

## Current migration

Existing Advanced SPI plugins remain supported only when they independently
satisfy Advanced admission. They are not templates or compatibility fallbacks
for ordinary plugin development.

Migration completes first for `minimal`, then for `ass-spec`. Each migration
must prove equivalent domain conclusions before old author-maintained mechanical
files or hooks are removed.

Frontend has completed the hard cut. `plugins/frontend-audit/` is its only
author source, and the aggregate distribution build compiles that Policy Pack
into the Simple SDK/common-review registration. No historical FUA-10 runtime
adapter or direct build path remains.
