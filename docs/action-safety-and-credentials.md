# Assayer Action Safety, Request Interception, and Credential Contract

| Metadata | Value |
|---|---|
| Document version | 1.0.0-draft |
| Date | 2026-08-30 |
| Status | Design converging |
| Owner | Host Core / Security Owner |

## 1. Purpose

This document turns “never perform a real dangerous action” and “credentials never enter the Agent” into a mechanically enforceable Host contract. Page labels, Agent intent, and HTTP methods are not sufficient grounds for allowing an action by themselves.

## 2. Safety Decision Layers

Every action passes four gates in order:

1. **Lifecycle gate**: current Scan, Object, and Case states allow the action;
2. **Intent gate**: action type and declared Agent purpose are allowed;
3. **Target gate**: target is an object verified by Host on the current page;
4. **Request gate**: every outbound browser request is independently classified and allowed before sending.

Any rejection stops the action. An upper-layer approval cannot override a lower-layer rejection.

## 3. Action Classes

| Class | Examples | Default policy |
|---|---|---|
| Observation | scroll, focus, read visible state | Allowed, with an Operation record. |
| Reversible navigation | expand, close, local tab switch, open detail | Conditionally allowed with a recovery path. |
| Synthetic input | enter synthetic values in edit state, trigger frontend validation | Conditionally allowed; record the original state and never submit. |
| Explicit write | save, submit, delete, publish, approve, upload, import | Refuse. |
| Unknown action | custom script, arbitrary selector, unclassifiable control | Refuse. |

Button text is used only for candidate intent classification. If a page disguises a write as “view”, the request gate must still block it.

## 4. Interception Before Request Sending

Host registers an interception hook at the browser network layer before sending. A request may not leave the browser context until classification completes.

### 4.1 Classification inputs

- URL, origin, and method;
- resource type;
- Content-Type and security-allowlisted headers;
- GraphQL operation type and operation name;
- whether multipart, file, or FormData is present;
- the Operation/Case that initiated the request;
- page action intent;
- frozen site policy and optional project-adapter result.

Request bodies may be held in Host memory for classification only. They must be sanitized first and never written to logs or model evidence packages.

### 4.2 Default policy

| Request | Policy |
|---|---|
| Same-origin, identified read-only query | May pass. |
| Cross-origin | Block navigation and business requests except explicitly allowed static resources. |
| GraphQL `mutation` | Block. |
| File upload, multipart, Beacon | Block. |
| WebSocket/SSE | Observe connection only by default; client business messages are blocked unless project policy proves read-only. |
| POST/PUT/PATCH/DELETE | Block by default; read-only POST requires an owner-approved project adapter. |
| GET/HEAD/OPTIONS | Still check target and context; method alone does not prove side-effect-free behavior. |
| Service Worker business request with no reliable attribution | Block and mark the Operation result unknown. |
| Unclassifiable | Block. |

When a project adapter relaxes policy, freeze its version and content digest at Scan start and include both in the ledger.

## 5. Blocking and Contamination Decisions

- Successfully canceled before sending: record `request_blocked`; action result is `rejected` or `failed_known`;
- Unable to prove that a request was not sent: Operation becomes `result_unknown`; stop ordinary actions and recover immediately;
- Possible persistent write observed leaving the browser: Scan becomes `failed`; all formal conclusions are invalid;
- Page-local state changed while request was blocked: still recover the Case;
- Blocked request URL, method, and sanitized classification reason may enter diagnostics; headers, bodies, cookies, and tokens may not.

Implementation constraint: persisted RequestObservation retains only URL without query/fragment, method, transport, whether sent, and classification result; request headers, bodies, cookies, and tokens never enter SQLite. Action string values retain only type and length before entering ActionAttempt/ReverseCase. An unconfigured action adapter is a deterministic pre-send failure, not `result_unknown`.

## 6. Timeouts and Retries

Every wait has an explicit budget, with final values supplied by runtime configuration and written to the Scan. The first version configures at least navigation, Operation, network-silence, targeted-recovery, refresh-replay, source-query, and Agent-turn budgets.

Default principles:

- Read operations retry only clearly transient errors, and only a bounded number of times;
- Browser and recovery actions are not replayed automatically; inspect the Operation first;
- Do not retry external requests that are non-idempotent or cannot be proven idempotent;
- Every retry class has a maximum count and total time budget;
- Over-budget operations return a deterministic error instead of waiting indefinitely.

## 7. Credential Input Channel

Credentials are not ordinary protocol messages. `start_audit` accepts only a one-time Host-generated `credentialHandle`; the Agent cannot see the associated value.

Recommended flow:

```text
1. User enters credentials through the Host local secure prompt
2. Host creates a one-time credentialHandle in an in-memory vault
3. Caller associates only the handle with the bootstrap request
4. Host login module consumes the handle
5. On success or failure, clear the credential value and handle immediately
6. Agent receives only loginStatus and sanitized diagnostics
```

Constraints:

- Never provide plaintext credentials through CLI arguments, environment variables, ordinary JSON, MCP parameters, or logs;
- Use the shortest practical in-memory TTL and allow one consumption only;
- Do not export browser cookies, tokens, or local/session storage;
- Login screenshots are disabled by default; if diagnosis is essential, mask all input and value regions first;
- Failure to clear credentials is a security-fatal error and fails the Scan.

The current implementation uses `LoginSecret` for Host username/password buffers. The Vault accepts typed secrets and zeroes them on expiry, discard, or Host shutdown. `LoginCoordinator` clears on success, failure, and adapter-exception paths; when clearing cannot be proven it returns `CREDENTIAL_CHANNEL_FAILED` and cannot accept login success. Immutable strings across Python/browser-driver boundaries cannot guarantee OS-level zero residue, so real adapters must also avoid caching, logging, or returning those strings.

## 8. Sanitization and Logging

Host applies structured sanitization before evidence is persisted:

- Headers: remove Authorization, Cookie, Set-Cookie, and project-declared secrets;
- URLs: remove sensitive query values such as token, code, and ticket;
- DOM/text: mask passwords, tokens, personal identifiers, and project-sensitive fields;
- Request bodies: do not persist by default; typed collectors may emit only a minimal summary;
- Screenshots: apply irreversible pixel masking to sensitive regions, never a reversible overlay;
- Logs: record only IDs, phase, outcome, duration, and sanitized reason codes.

Structured Evidence may set `sanitized=true` only after sanitization succeeds and `sanitizationPolicyVersion` is recorded. Images have a separate `sanitizationStatus`: controlled B07b screenshots may be `not_performed`, but never `sanitized`, and cannot enter formal `issue_found`. Unknown or inconsistent sanitization state rejects capture.

## 9. Security Errors

| Code | Meaning | Result |
|---|---|---|
| `ACTION_BLOCKED` | Action intent or target is disallowed | Case may gather evidence or become needs_review |
| `REQUEST_BLOCKED` | Outbound request intercepted before sending | Recover the Case |
| `REQUEST_RESULT_UNKNOWN` | Cannot prove whether request was sent or applied | Recover; if cleanliness cannot be proven, Scan failed |
| `CREDENTIAL_CHANNEL_FAILED` | Credential handle or clearing failed | Scan failed |
| `SANITIZATION_FAILED` | Evidence cannot be reliably sanitized | Do not save evidence; affected rule needs_review |
| `CROSS_ORIGIN_BLOCKED` | Navigation or request to disallowed origin | Record skip/block |

## 10. Design Acceptance Questions

The safety design must answer clearly:

- What happens when a button named “Search” actually sends a mutation?
- How is absence of persistent side effect proven after a request timeout?
- How is a click-before-response browser crash converged?
- How are Beacon or WebSocket writes blocked?
- Can a user password appear in MCP, the command line, logs, screenshots, or evidence payloads?
- Does the sanitizer fail closed when it cannot identify a custom sensitive field?
