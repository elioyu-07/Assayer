# Assayer Plugin Development Guide

This guide describes the only ordinary-plugin authoring path. The normative
authority is [Platform Constitution v2](platform-constitution-v2.md) and the
compiled contract is defined by [Plugin Contract v1](plugin-contract-v1.md).

## Author surface

An ordinary plugin is a declaration package, not a Python package:

```text
my-plugin/
├── plugin.yaml
├── checks.yaml
├── semantic-review.md
└── cases/
    ├── ready.yaml
    └── issue.yaml
```

The declarations describe domain meaning only:

- `plugin.yaml`: identity, business version, and a platform-supported input kind;
- `checks.yaml`: stable Check and Dimension meaning, applicability, outcomes,
  severity, remediation, and optional deterministic predicates;
- `semantic-review.md`: evidence-based guidance for semantic judgment; and
- `cases/`: positive, negative, unknown, and not-applicable business examples.

Ordinary plugins contain zero Python. They must not ship parsers, source
clients, Providers, Agent prompts, platform schemas, transport, lifecycle
hooks, result mappers, persistence, or external effects.

## Declaration example

```yaml
# plugin.yaml
id: dev.example.quality
name: Example quality
description: Reviews supported documents for delivery risk.
version: 1.0.0
input: document
checks: checks.yaml
instructions: semantic-review.md
```

```yaml
# checks.yaml
checks:
  - id: PERM-001
    title: Permission denial behavior
    description: Protected operations define roles, scope, and denial responses.
    applicability: Documents that define protected operations or data.
    default_severity: P2
    recommendation: Define roles, scope, and denial responses.
    unknown_when: The relevant section or referenced authority is unavailable.
```

The platform validates these declarations and compiles one normalized,
versioned contract. It derives the manifest, Provider requirements, review
plan, Agent guidance, validation rules, business-case acceptance, package
metadata, contract digest, and release identity. Authors do not maintain a
second generated source of truth.

## Complete review semantics

For every supported source element, the Host plans the product of element,
Check, and Dimension. The Agent must close every planned atom with exactly one
of `satisfied`, `violated`, `not_applicable`, `unknown`, or `blocked`.
Cases must make the expected domain meaning explicit; an absent response is
never a pass. `not_applicable` may be hidden from a user report but remains in
the durable ledger.

Semantic guidance must explain what evidence distinguishes each outcome and
what information makes an outcome unknown or blocked. It must not describe
platform IDs, cursors, checkpoints, digests, recovery, or transport calls.

## Verification and artifact lifecycle

Run the Host-owned verification gate:

```text
assayer plugin verify .
```

The gate validates the source boundary, declarations, semantic guidance,
complete business cases, generated contract, digest, exhaustive review
acceptance, and artifact identity. Success produces exactly:

```text
.assayer/verified/compiled-plugin.json
```

Only that exact artifact may be installed, enabled, or published. A failed
verification invalidates any previous artifact for the same source revision.
The source tree is never copied into the installation store. Ordinary plugins
do not produce Python distributions; Provider packages follow their own
platform-owned distribution contract.

Inside Codex, the `assayer-plugin-development` Skill calls the Host-owned
verification tool. Verification does not install or publish the plugin.

## Platform capability boundary

If a plugin appears to need parsing, source access, navigation, persistence,
parallel scheduling, recovery, or an external effect, that is a platform
capability gap. The plugin author must not fill it with private code or an
alternate interface. Request a new platform capability or Provider instead.

The number of internal Agents is not part of the plugin contract. The platform
owns orchestration, bounded batches, Evidence, Coverage, Ledger, Result,
authorization, installation, and release. The plugin supplies requirements and
meaning only.
