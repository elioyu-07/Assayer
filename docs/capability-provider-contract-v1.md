# Assayer Capability Provider Contract v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-03 |
| Status | Frozen for M2; provider conformance tracked by M3 |
| Owner | Assayer maintainers |

## 1. Purpose

A capability provider is a controlled adapter for one source or execution
surface: for example a browser, file system, repository, API, database, or log
stream. Providers make facts available to plugins without moving domain
semantics into the platform kernel.

## 2. Provider descriptor

Each provider publishes a descriptor containing:

| Field | Meaning |
|---|---|
| `providerId` and `version` | Stable provider identity and semantic version |
| `capabilities` | Explicit names such as `structured_read`, `visual_read`, or `safe_interaction` |
| `scopeSchema` | Business-input shape accepted by the provider |
| `authorization` | User-consent and platform-policy requirements |
| `limits` | Timeout, byte/item budget, concurrency, and rate limits |
| `evidenceKinds` | Evidence forms the provider can produce |
| `failurePolicy` | Known failure codes and whether retry is safe |
| `algorithmVersions` | Identity, normalization, sanitization, or source-digest algorithms used |

Capabilities are allowlisted. A descriptor does not authorize itself; the Host
computes the effective profile as:

```text
platform policy ∩ provider descriptor ∩ plugin requirements ∩ user scope
```

## 3. Request and response rules

Provider requests are Host-created, scoped to the current Run and WorkItem,
and carry timeout, budget, and idempotency metadata. Providers return either a
bounded fact response or a classified failure. A response must identify the
source identity and state digest used to collect it. Raw credentials, hidden
model reasoning, and unbounded source bodies are never ordinary Agent output.

The Host freezes provider ID and version, Check ID and version, capability,
source state, scope, and effective limits in each request. A response must echo
the request, provider, version, and capability identity. Timeout is a bounded
provider-operation budget, never a fixed lifetime for the complete Run.

Providers may not make a compliance decision, alter a Check, write to the
platform ledger, or widen authorization. Write-capable providers require a
separate explicit platform safety contract; audit providers default to
read-only.

## 4. Failure and evidence semantics

The provider distinguishes at least:

- `capability_unavailable`: required source capability is absent;
- `authorization_denied`: the requested scope is not authorized;
- `timeout` or `budget_exceeded`: the bounded operation did not finish;
- `source_changed` or `stale_state`: returned facts cannot be reused;
- `source_error`: the target reported an error;
- `result_unknown`: completion cannot be proven and blind replay is forbidden.

These failures are diagnostic facts. The plugin's declared missing-capability
outcome and the Host's result gates determine whether the WorkItem is blocked,
needs review, or remains incomplete; a provider never chooses a business
result.

Every accepted fact becomes immutable Host Evidence bound to the Run,
WorkItem, Check, provider version, source identity, and relevant state digest.
Caching is valid only when all of those keys and invalidation signals match.

## 5. Provider compatibility

Provider major changes that alter capability meaning, authorization, evidence
semantics, or failure interpretation require a new major version. Additive
capabilities and optional metadata are minor changes. Historical Evidence keeps
the provider version that produced it and is not reinterpreted by an upgrade.
