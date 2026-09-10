# Assayer Plugin Protocol v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-10 |
| Status | Frozen; machine contract in `schemas/plugin-protocol-envelope.schema.json` |
| Owner | Assayer maintainers |
| Authority | Platform Constitution v1 §3.13, Platform--Plugin Boundary Contract v1 §1/§4/§5 |
| Applies to | Platform kernel, plugin and capability-provider bindings, Agent adapters |

## 1. Purpose

This document freezes the **binding-neutral** message contract between the Host
and an audit plugin. It defines the operations, the request/response envelope,
and the error shape. It deliberately says nothing about *how* a message
travels: the same envelope is carried by an in-process call and by an
out-of-process transport. Per Boundary Contract v1 §1, nothing here requires
separate processes or repositories.

The plugin protocol is an evolution of the interactive handshake already
declared in `plugin_compatibility.py`. The protocol version is the value of
`HOST_PROTOCOL_VERSION`; a plugin declares its supported range in its manifest
`compatibility` block (see `schemas/plugin-manifest.schema.json`). The four
independent versions (distribution, platform API, protocol, SDK) and their
relationships are defined in
[`plugin-version-axes-v1.md`](plugin-version-axes-v1.md).

## 2. Binding neutrality

A **binding** is the transport that carries envelopes. Two bindings are
permitted by this protocol and must be interchangeable:

- in-process: the Host calls an already-created plugin object;
- out-of-process: the Host sends the envelope to a plugin process and reads a
  response envelope.

The kernel and the interactive controller MUST address a binding, never a
concrete plugin implementation. The binding interface is intentionally left
unimplemented here so that opting into out-of-process later is additive rather
than a rewrite.

## 3. Envelope

Every operation uses one request envelope and one response envelope, defined in
[`plugin-protocol-envelope.schema.json`](../schemas/plugin-protocol-envelope.schema.json).

### 3.1 Request

```json
{
  "protocolVersion": "1.2.0",
  "requestId": "req-0001",
  "pluginId": "dev.assayer.frontend-audit",
  "runId": "run-0001",
  "checkId": "FUA-10",
  "operation": "inspect",
  "deadlineMs": 30000,
  "input": {}
}
```

- `protocolVersion` (required): semantic version; must fall inside the plugin's
  declared range.
- `requestId` (required): stable identity of this request; a retry reuses it.
- `pluginId` (required): the addressed plugin.
- `runId`, `checkId` (optional): scope the operation to a frozen Run and Check.
- `operation` (required): one of the operations in §4.
- `deadlineMs` (optional): a local operation budget, not a Run deadline.
- `input` (required): operation-specific JSON payload.

### 3.2 Response

```json
{
  "protocolVersion": "1.2.0",
  "requestId": "req-0001",
  "status": "ok",
  "output": {}
}
```

- `status` is `ok`, `rejected`, or `failed`.
- An `ok` response MUST carry `output`.
- A `rejected` or `failed` response MUST carry `error`:

```json
{
  "protocolVersion": "1.2.0",
  "requestId": "req-0001",
  "status": "rejected",
  "error": {
    "code": "PLUGIN_CONTRACT_VIOLATION",
    "message": "Inspect input is missing the frozen Check identity",
    "retryable": false,
    "requiredNextStep": "Retry with the Check identity returned by discover"
  }
}
```

`rejected` means the Host must not retry without a corrected request (fail
closed). `failed` means a classified provider/transport failure; the platform's
existing recovery path applies.

## 4. Operations

| Operation | Direction | Input | Output |
|---|---|---|---|
| `describe` | Host → plugin | `{}` | plugin manifest (frozen identity) |
| `discover` | Host → plugin | scope + capability context | `WorkItem[]` |
| `inspect` | Host → plugin | `WorkItem[]` + frozen `Check` | `InvestigationPacket[]` |
| `restore` | Host → plugin | recovery `Case` | `RecoveryResult` (optional) |
| `validate_review_checkpoint` | Host → plugin | checkpoint + selected immutable items | domain invariants (optional) |
| `map_domain_result` | Host → plugin | submitted `DomainResult` | Decision projection (optional) |
| `summarize` | Host → plugin | canonical result | namespaced `domainExtension` (optional) |

`describe` and the domain operations are pure functions of the input and the
plugin's frozen manifest. The Host owns Run identity, Evidence, paging,
checkpoint persistence, Decision commit, replay, resume, and publication; none
of those are fields of a request or response.

The Agent-facing submission inside `map_domain_result` is the
`DomainResultContract` declared by the plugin and frozen by the Host for the
Run (see [`plugin-domain-result-contract.schema.json`](../schemas/plugin-domain-result-contract.schema.json)).

## 5. Compatibility

1. A plugin declares `protocolMinVersion`/`protocolMaxVersion` and
   `sdkMinVersion`/`sdkMaxVersion` in its manifest `compatibility` block.
2. Before a Run starts, the Host negotiates with
   `negotiate_plugin_compatibility`. An unsupported protocol major or SDK range
   is rejected before discovery with `PLUGIN_PROTOCOL_INCOMPATIBLE` /
   `PLUGIN_SDK_INCOMPATIBLE`.
3. A platform-only implementation or transport optimisation MUST NOT change
   this envelope or require a plugin change (Constitution §3.13). The binding
   absorbs such changes.

## 6. Conformance

An implementation conforms to this protocol when:

- every Host→plugin operation is one of the §4 operations;
- every message validates against `plugin-protocol-envelope.schema.json`;
- an `ok` response carries `output` and a non-`ok` response carries `error`;
- the same plugin produces equivalent results under every binding that carries
  this envelope;
- protocol and SDK ranges are negotiated before execution.

## 7. Non-goals

- This protocol does not require a separate process, a separate repository, or
  a serialization format. A binding may carry envelopes as in-memory objects.
- This protocol does not define domain semantics, coverage, or decision rules;
  those belong to the plugin's manifest and `DomainResultContract`.
- This protocol does not change any platform invariant: fail closed, the Host
  trust boundary, the single-source ledger, evidence closure, or idempotency.
