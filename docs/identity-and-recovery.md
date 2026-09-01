# Assayer Page, Object Identity, and Recovery Contract

| Metadata | Value |
|---|---|
| Document version | 1.0.0-draft |
| Date | 2026-08-30 |
| Status | Design converging |
| Owner | Host Core Owner |

## 1. Purpose

This document defines logical PageState and AuditObject identity, rebinding, Case recovery baselines, equivalence, and failure propagation. It does not require identical pixels or DOM. It proves that investigation still targets the same logical object and that a Case left no state contamination affecting later work.

## 2. Identity Algorithm Versions

Every Scan freezes `pageIdentityAlgorithmVersion`, `objectIdentityAlgorithmVersion`, and `recoveryPolicyVersion`. Versions enter ScanRun and the final ledger. Incompatible behavior changes increment the major version; historical ledgers are interpreted using their recorded versions.

## 3. PageState Identity

PageState is an immutable observation snapshot with a Host-generated `pageStateId`.

### 3.1 Required dimensions

- Normalized origin;
- Normalized route, including query parameters that participate in page identity;
- Page layer: page/dialog/drawer/tab/detail/edit;
- Parent PageState identity;
- Active tab or equivalent region;
- Key overlays affecting object reachability.

### 3.2 Conditional dimensions

- Case-related form value classes;
- Expanded rows, pagination, filters, and selections;
- Permission or business-mode markers that affect rule decisions;
- Relevant pending requests.

### 3.3 Dynamic material forbidden from direct identity use

- Timestamps, random IDs, and trace IDs;
- Animation frames, cursor, hover, and other transient visuals;
- Background polling unrelated to the current object and Case;
- Raw whole-page DOM hash;
- Identity claims injected through page content.

Host stores `identityMaterialDigest` and a structured explanatory summary without credentials or unsanitized business data.

## 4. AuditObject Identity

AuditObject is a logical object, not a long-lived DOM node. Identity combines, in priority order:

1. Host-verified component or stable business identifier;
2. ARIA role, accessible name, and associated label;
3. Owning business region, table column, or form path;
4. Normalized structural path and adjacent stable anchors;
5. Visible-text summary;
6. Geometry only as the final disambiguator, never as sole identity.

`hostLocatorId` is a short-lived handle within the current browser instance and is not persisted as a cross-run selector. `fingerprint` digests normalized identity material but does not guarantee relocation by digest alone.

## 5. Rebinding

After navigation, refresh, tab switch, overlay change, rerender, or any action that may replace DOM, old node references are stale. Host rediscovers candidates and returns one result:

| Result | Definition | Next behavior |
|---|---|---|
| `matched` | Exactly one candidate satisfies every required identity dimension. | Create a new locator handle and continue. |
| `not_found` | No candidate satisfies required dimensions. | Current operation or recovery fails. |
| `ambiguous` | Multiple candidates satisfy required dimensions and cannot be disambiguated reliably. | Never choose the “closest”; stop this object. |
| `changed` | A related object exists, but rule-relevant semantics changed. | Create a new candidate; never impersonate the original object. |

Rebinding stores candidate count, matched dimensions, and exclusion reasons as diagnostics and never exposes executable selectors to the Agent.

## 6. Case Recovery Baseline

Before any action, `begin_case` atomically saves at least:

- Initial PageState and page-identity summary;
- Current object identity summary and rebinding material;
- Active tab, overlays, and expanded regions;
- Controls this Case may change;
- Current URL/route;
- Relevant pending-request set;
- Write-request count fixed at zero;
- Safe entrypoint chain for recovery after refresh.

The baseline is trimmed to the Case's declared impact. Host may expand required checks; Agent cannot narrow safety checks.

## 7. Action Log and Inverse Actions

Every successful action records Operation/action ID, pre-action `runRevision`, target object, pre-action evidence, actual result, recovery mode (`inverse`, `noop`, or `refresh_only`), and Host-internal inverse parameters.

Agent cannot provide inverse selectors or scripts. Synthetic input records a safe restoration handle for the original value. Sensitive originals stay in Host memory and never enter the ledger.

## 8. Recovery Algorithm

```text
1. Freeze new ordinary action requests
2. Wait within budget for relevant read-only requests
3. Execute targeted inverse actions in reverse log order
4. Rebind page and object
5. Compare every required dimension
6. All match -> restored
7. No mismatch but any unknown -> uncertain
8. Critical mismatch, write request, or inverse failure -> failed
9. uncertain or failed -> refresh original URL and replay safe entrypoint chain
10. Verify every dimension again and produce the final result
```

Checks include `url_route`, `page_layer`, `active_tab`, `overlay_state`, `control_state`, `object_identity`, `pending_requests`, `write_request`, and supplementary `local_visual`.

`restored` requires every applicable required dimension to be `match`, with no `unknown`. Visual similarity cannot override structural or object-identity `unknown/mismatch`.

## 9. Failure Propagation

| Situation | Case | Object | Scan | Prepared decision |
|---|---|---|---|---|
| Targeted recovery fails; refresh succeeds | completed | continue | continue | may commit |
| Object cannot be uniquely rebound; no environmental contamination | restore_failed | blocked | usually partial | invalidated |
| Pending request cannot be proven finished | restore_failed | blocked | failed | all invalidated |
| Persistent write is detected | invalidated | blocked | failed | all invalidated |
| Browser crash makes state unprovable | invalidated | blocked | failed | all invalidated |

Only Host decides recovery status and contamination scope. Agent cannot downgrade failure to success with natural-language rationale.

## 10. Completion Criteria

Identity and recovery qualify only when:

- The same object reliably rebinds after rerender;
- Two indistinguishable similar objects consistently return `ambiguous`;
- Dynamic page content does not create meaningless whole-page inequality;
- Every relevant state changed by a Case enters required checks;
- Visual similarity cannot hide a write request, unknown pending request, or lost object;
- Every recovery result can be recomputed from ledger baseline, actions, and checks.

Host Core implements this deterministic recovery barrier: incomplete targeted `match` always enters refresh replay; unknown pending/write requests fail the Scan; Host restart converges orphan action/recovery Operations as `result_unknown`. B04 added Playwright snapshots, a Session locator registry, and unique pre-action binding. B05 added safe actions, pre-send interception, and strict post-action rebinding. B06 connected inverse actions, refresh replay, network-convergence proof, and nine-dimensional real-browser checks to the same Session without weakening these criteria.
