# Assayer Plugin Protocol v1

| Metadata | Value |
|---|---|
| Document version | 1.1.0 |
| Date | 2026-09-13 |
| Status | Historical generated-binding protocol; superseded for ordinary plugins |
| Owner | Assayer maintainers |
| Authority | Platform Constitution v1 §3.13, Platform--Plugin Boundary Contract v1 §1/§4/§5 |
| Applies to | Historical platform bindings and capability-provider evidence only |

> **Superseded.** Ordinary plugins do not implement this protocol. They provide
> declarations and are installed only as `compiled-plugin.json`; Provider
> runtime bindings are governed by the Provider contract.

## 1. Purpose

This document freezes the typed invocation contract between the Host and an
Advanced SPI audit plugin. It defines operation ownership and compatibility;
it does not publish a second JSON envelope beside the executable SDK types.
Per Boundary Contract v1 §1, nothing here requires separate processes or
repositories.

Ordinary Policy Pack and Simple SDK authors do not declare, implement, or test
this protocol. The SDK compiler generates any required binding and
compatibility metadata. The operations below describe the current Advanced SPI
migration contract and internal generated boundary, not the Simple author
surface.

The plugin protocol is an evolution of the interactive handshake already
declared in `plugin_compatibility.py`. The protocol version is the value of
`HOST_PROTOCOL_VERSION`; the compiler or an Advanced SPI publisher declares the
supported range in the generated manifest `compatibility` block (see
`src/assayer_plugin_sdk/schemas/plugin-manifest.schema.json`). The four
independent versions (distribution, platform API, protocol, SDK) and their
relationships are defined in
[`plugin-version-axes-v1.md`](plugin-version-axes-v1.md).

## 2. Current binding

The current Advanced SPI is an in-process typed binding. The Host selects a
validated `PluginRegistration`, constructs the registered implementation, and
invokes it only through the operations below. The Host creates platform
identity and frozen typed inputs, validates every returned value, and converts
contract or runtime failures through the platform error policy.

An out-of-process plugin binding is not currently published. Adding one would
require its own versioned transport design and generated codec; it MUST NOT be
inferred from obsolete hand-maintained JSON examples.

## 3. Operations

| Operation | Direction | Input | Output |
|---|---|---|---|
| `describe` | Host → plugin | `{}` | plugin manifest (frozen identity) |
| `discover` | Host → plugin | scope + capability context | `WorkItem[]` |
| `inspect` | Host → plugin | `WorkItem[]` + frozen `Check` | `InvestigationPacket[]` |
| `restore` | Host → plugin | recovery `Case` | `RecoveryResult` (optional) |
| `map_domain_result` | Host → plugin | submitted `DomainResult` | Decision projection (optional) |
| `summarize` | Host → plugin | canonical result | namespaced `domainExtension` (optional) |

`describe` and the domain operations are pure functions of the input and the
plugin's frozen manifest. The Host owns Run identity, Evidence, paging,
Decision commit, replay, resume, and publication; none of those are fields of a
request or response.

The Agent-facing submission inside the current Advanced
`map_domain_result` operation is the generated or Advanced SPI typed
`DomainResultContract` frozen by the Host for the Run.
The Simple target replaces this complete-WorkItem contract with compiler-owned
common ReviewBatch contracts.

## 4. Compatibility

1. The compiler, or an Advanced SPI publisher, declares
   `protocolMinVersion`/`protocolMaxVersion` and
   `sdkMinVersion`/`sdkMaxVersion` in the generated manifest compatibility
   block. An ordinary author does not maintain these values.
2. Before a Run starts, the Host negotiates with
   `negotiate_plugin_compatibility`. An unsupported protocol major or SDK range
   is rejected before discovery with `PLUGIN_PROTOCOL_INCOMPATIBLE` /
   `PLUGIN_SDK_INCOMPATIBLE`.
3. A platform-only implementation or transport optimisation MUST NOT change
   this invocation contract or require a plugin change (Constitution §3.13).
   The Host binding absorbs such changes.

## 5. Conformance

An implementation conforms to this protocol when:

- every Host→plugin operation is one of the §3 operations;
- the Host invokes only typed operations from §3 and converts boundary
  failures into the platform error policy;
- batch and interactive execution produce equivalent formal Decisions for the
  same frozen inputs where both modes are declared;
- protocol and SDK ranges are negotiated before execution.

## 6. Non-goals

- This protocol does not require or define a separate process, repository, or
  serialization format.
- This protocol does not define domain semantics, coverage, or decision rules;
  ordinary semantics belong to domain declarations and invariants, while the
  compiler generates the internal common-review contract.
- This protocol does not change any platform invariant: fail closed, the Host
  trust boundary, the single-source ledger, evidence closure, or idempotency.
