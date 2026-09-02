# Assayer

Assayer is a frontend-quality audit agent for test and staging web sites. The Host Core, real Chromium adapter, dynamic JSON/MCP Runtime Router, project Skill, model-independent agent loop, and Codex entrypoint are connected end to end. The production path uses LLM-owned semantic decisions; the deterministic semantic evaluator is not part of the product runtime.

## Current status

The product goal is a complete end-to-end user journey, not a collection of isolated components. The journey gates and Definition of Done are documented in [User Journey and Definition of Done](docs/user-journey-and-definition-of-done.md).

The J01 delivery skeleton is in [`plugins/assayer`](plugins/assayer). Build a release archive with:

```bash
python3 scripts/build_plugin_bundle.py --output ./dist
```

Inspect or run plugins registered in the current installation:

```bash
assayer plugins list --json
assayer plugins run \
  --plugin assayer.config-quality \
  --check CFG-001 \
  --scope-json '{"files":[{"path":"settings.json"}]}'
```

The built-in Spec quality plugin inspects Markdown requirements through the
domain-neutral interactive lifecycle. Its deterministic runtime emits bounded
candidate evidence; an Agent must submit the semantic decision. The frontend
audit tools remain compatibility aliases for the browser plugin.

The builder packages the offline runtime, rules, schemas, and MCP server and verifies the bundle in a temporary environment. See [J01 install and delivery](docs/j01-install-delivery.md) for platform boundaries.

Implemented product foundations include:

- Host-owned Scan/Operation state, credential handling, page discovery, object identity, Cases, safe actions, recovery barriers, Evidence, decision transactions, audit completion, ledger export, and JSON/Markdown reports.
- A dynamic Runtime Router with Scan isolation, lease supervision, bounded recovery, and a formal Agent entrypoint.
- A real-browser read-only adapter that captures structured DOM context and object-level visual Evidence while keeping browser handles opaque.
- FUA-10 five-state regression coverage and a full gate that executes Chromium and MCP tests without silent skips.
- Layered observability: durable ledger, runtime event stream, public decision trace, diagnostics, and integrity manifest.
- Multilingual target-page recognition. Locale terms used by browser probes are isolated in runtime resources; Assayer's authored product and engineering text is English.

## Run a formal audit

Provide an HTTP(S) URL. Codex loads the `assayer-audit` Skill and uses the local product MCP facade; users and models do not construct protocol envelopes.

```bash
assayer audit 'http://localhost:8081/#/lease-mock' --output-root ./assayer-output
```

The formal path fails explicitly when Codex, MCP, or the browser runtime is unavailable. It never silently falls back to smoke mode.

For a non-publishable Host fact diagnostic, use `smoke` explicitly:

```bash
assayer smoke 'http://localhost:8081/#/lease-mock' --output-dir ./assayer-smoke-output
```

## Run the deterministic harness

The harness is for contract demonstrations and CI only; it is not a real-site audit and does not accept real credentials.

```bash
assayer-harness --output-dir ./audit-output
```

The output directory contains `audit-ledger.json`, derived JSON/Markdown reports, `runtime-events.jsonl`, `observability-manifest.json`, and required screenshots.

The low-level JSON Lines transport is available for integrations:

```bash
assayer-json --stdio --output-root ./assayer-output < requests.jsonl
```

The local stdio MCP server is available after installing optional dependencies:

```bash
pip install 'assayer[browser,mcp]'
assayer-mcp --output-root ./assayer-output
```

## Test gates

Fast tests cover deterministic unit and protocol regressions:

```bash
python scripts/run_tests.py fast
```

Full acceptance starts Chromium and executes MCP SDK tests. Missing dependencies, browser startup failure, or skipped tests fail the gate:

```bash
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/run_tests.py full
```

See [C07.2 real-link test gate](docs/implementation-slice-032.md) and [observability governance](docs/observability-governance.md).

## Recommended reading

1. [Product contract](docs/product-contract.md)
2. [User journey and Definition of Done](docs/user-journey-and-definition-of-done.md)
3. [J01 install and delivery](docs/j01-install-delivery.md)
4. [J02 activation and discovery](docs/j02-activation-and-discovery.md)
5. [Design governance](docs/design-governance.md)
6. [Top-level architecture](docs/architecture.md)
7. [LLM agent orchestration](docs/llm-agent-orchestration.md)
8. [Domain model and lifecycle](docs/domain-model-and-lifecycle.md)
9. [Host–Agent protocol](docs/host-agent-protocol.md)
10. [Object identity and recovery](docs/identity-and-recovery.md)
11. [Action safety and credentials](docs/action-safety-and-credentials.md)
12. [Evidence and decision integrity](docs/evidence-and-decision-integrity.md)
13. [Rule contract](docs/rule-contract.md)
14. [Verification and traceability](docs/verification-and-traceability.md)
15. [Tool contracts](docs/tool-contracts.md)
16. [Browser and MCP integration plan](docs/browser-mcp-integration-plan.md)
17. [LLM agent integration plan](docs/llm-agent-integration-plan.md)
18. [Observability governance](docs/observability-governance.md)
19. [Plugin development contract](docs/plugin-development.md)
20. [Platform project plan](docs/project-plan.md)

## Repository layout

```text
docs/       Product, architecture, protocol, and design documents
rules/      Rule templates and versioned FUA rules
schemas/    JSON Schemas for persisted data and protocol messages
examples/   Ledger and protocol examples
src/        Host Core and model-independent Agent loop
tests/      Host Core, browser, MCP, and contract tests
```

## Normative sources

When documents conflict, follow the authority order in [Design governance](docs/design-governance.md). Examples, tests, and derived reports cannot override the product contract, safety invariants, protocol, or schemas.
