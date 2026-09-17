# Operator Release Gate v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-14 |
| Status | Active release-gate profile |
| Owner | Product Owner / Release Engineering |

> **Plugin-specific acceptance profile.** This gate exercises one installed
> plugin and its controlled fixture. It is not a generic platform contract;
> generic release laws live in the Platform Constitution and v1 contracts.

## 1. Authority and scope

This document is the executable operator-acceptance profile for J04, J05, and
J08. The authoritative journey and completion semantics remain in
[Alpha User Journey and End-to-End Definition of Done](user-journey-and-definition-of-done.md).
This profile fixes how those requirements are exercised, recorded, and admitted
to a release. It does not create a second product journey.

Unit, integration, MCP, deterministic CLI, and package tests are necessary
release evidence, but they do not satisfy this operator gate. A release is not
operator-accepted until a person has exercised the supported Codex product
boundary from a clean profile and the resulting evidence bundle passes this
profile.

## 2. Status model

Every scenario and the aggregate gate use exactly one status:

| Status | Meaning | Release effect |
|---|---|---|
| `passed` | Every required observation is present and supported by retained evidence | May contribute to release admission |
| `failed` | The product journey completed or terminated and violated at least one required observation | Blocks release |
| `blocked` | The journey could not reach a product verdict because an external prerequisite, model service, network, or clean environment was unavailable | Does not pass and blocks final admission until rerun |

`implemented`, `automated`, `attempted`, and `previously passed` are evidence
descriptions, not operator-gate statuses. A blocked external service is not a
product failure, but it is never recorded as a pass.

## 3. Clean operator environment

Each accepted execution MUST use:

- a newly created `CODEX_HOME` with no inherited personal Skills, plugins, MCP
  configuration, or session history;
- an isolated home/cache root, Assayer plugin store, and Run output root;
- an Assayer bundle installed through the supported Codex Marketplace path;
- an immutable, recorded test input containing only controlled synthetic or
  already-sanitized data; and
- recorded OS, architecture, Python, Codex, Assayer, domain-plugin, and test
  input identities.

The execution MUST NOT import packages or configuration from the development
checkout, reuse an earlier Run directory, manually construct internal protocol
fields, or repair the ledger between steps.

## 4. Gate inventory

| Gate | Required scenario | Required observation |
|---|---|---|
| `OPR-J04-B01` | One clean successful natural-language audit | Correct Skill/tool routing, continuous bounded progress, explicit waiting owner, terminal convergence, no internal protocol input |
| `OPR-J04-F01` | Three consecutive independent Runs | Unique Run identities, no stale-state reuse, no silent skip or downgrade, and at least one diagnosable partial or failed Run followed by a clean retry |
| `OPR-J05-COMPLETED` | Valid completed audit | Plain-language conclusion, covered scope, independent problems, evidence, remediation, and report location agree with the canonical result |
| `OPR-J05-NEEDS-REVIEW` | Audit with missing information | Concrete missing information and next action are named; no unsupported formal conclusion is published |
| `OPR-J05-PARTIAL` | Bounded partial audit | Completed and uncovered scope, blocker, validity boundary, and next action are explicit |
| `OPR-J05-FAILED` | Startup or runtime failure | Failure ownership and diagnostics are visible; invalidated Decisions are excluded from the formal result |
| `OPR-J08-LIFECYCLE` | Install, inspect, run, upgrade, rollback, uninstall, restart | Plans and authority are visible, versions are exact, rollback is safe, uninstall cleans private state, and restarted Codex observes the durable final state |

`OPR-J04-B01` is the first operator baseline. Passing it does not close J04 or
J05. J04 closes only after `OPR-J04-F01` passes. J05 closes only after all four
J05 scenarios pass. J08 closes only after `OPR-J08-LIFECYCLE` passes.

## 5. Required evidence bundle

Each execution is stored outside the source checkout under one immutable
directory named with UTC timestamp and a random execution identifier:

```text
operator-acceptance/<timestamp>-<execution-id>/
├── environment.json
├── scenario.json
├── gate-results.json
├── controlled-prompts.md
├── artifact-index.json
└── artifacts/
    ├── platform-ledger.json
    ├── platform-events.jsonl
    ├── platform-run.log
    ├── canonical-result.json
    └── audit-report.md
```

The bundle records:

| Record | Required content |
|---|---|
| Environment | Timestamp, timezone, OS, architecture, Python, Codex, Assayer, domain-plugin, profile root identity, and clean-environment declaration |
| Scenario | Gate ID, controlled acceptance prompt, input digest, expected observations, operator identity or role, and start/end times |
| Gate results | One result per required observation with `passed`, `failed`, or `blocked`, plus references to retained artifacts |
| Artifact index | Relative path, media type, SHA-256, privacy classification, and producing Run ID |
| Run evidence | Run ID, terminal status, progress history, result/report references, lifecycle transaction IDs when applicable, and recovery/retry identity |

Only controlled acceptance prompts may be retained verbatim. Raw user data,
credentials, cookies, authorization values, hidden model reasoning, raw page
content, and unsanitized screenshots MUST NOT enter the bundle. Redaction MUST
be declared; it MUST NOT alter a formal result or its identity.

Missing required evidence makes the affected observation `failed`. Unavailable
external infrastructure before a product verdict makes it `blocked`.

## 6. Baseline execution sequence

The first J04/J05 baseline uses the following natural-language intent without
manual MCP configuration or protocol payloads:

Prepare the isolated workspace with the exact release candidate and controlled
input. The output root MUST be outside the source checkout:

```bash
python scripts/prepare_operator_acceptance.py \
  --output-root /absolute/operator-evidence-root \
  --release-artifact /absolute/release/assayer.whl \
  --controlled-input /absolute/fixtures/spec.md
```

The command creates a path-safe evidence skeleton plus a private launch
environment. `gate-results.json` is deliberately absent until the real product
journey produces an operator verdict; preparation is not acceptance evidence.

```text
Install ass-spec from the configured Assayer catalog.
Use ass-spec to review the controlled spec.md input.
Show the conclusion and the full report location.
```

The operator records every visible product response, the selected Skill and
tool names, confirmation boundaries, progress updates, Run identity, terminal
result, and report location. The Host artifacts are copied by relative identity
into the evidence bundle and hashed before the isolated environment is removed.

## 7. Release admission

Operator admission requires all of the following:

1. `OPR-J04-F01` is `passed`;
2. every J05 state gate is `passed`;
3. `OPR-J08-LIFECYCLE` is `passed`;
4. the evidence bundle contains no failed privacy or artifact-integrity check;
5. the tested Assayer build is byte-identical to the release candidate; and
6. the automated CI support matrix required for that release is green.

An evidence bundle belongs to one exact Assayer release candidate and cannot be
silently carried forward to a different artifact. Reuse requires an explicit,
versioned release policy and proof that the affected product boundary did not
change; no such reuse policy exists in v1.

## 8. Evidence record

[Codex Natural-Language Plugin Lifecycle Acceptance](codex-natural-language-plugin-lifecycle-acceptance.md)
is the historical and current execution record. It MUST reference this profile
for pass/fail decisions and MUST NOT redefine the gate or treat deterministic
coverage as operator acceptance.
