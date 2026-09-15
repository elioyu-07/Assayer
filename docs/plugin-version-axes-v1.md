# Assayer Plugin Version Axes v1

| Metadata | Value |
|---|---|
| Document version | 1.1.0 |
| Date | 2026-09-13 |
| Status | Frozen; Simple author view and generated compatibility added |
| Owner | Assayer maintainers |
| Authority | Platform Constitution v1 §5, Platform--Plugin Boundary Contract v1 §10 |

## 1. Why this document exists

Assayer carries four independent versions. Before this document they were used
interchangeably, so a plugin could not state a precise compatibility range and
a platform release could not be classified. This document fixes the meaning,
owner, and compatibility rule of each axis.

## 2. The four axes

| Axis | Symbol | Current value | Format | Owner | Meaning |
|---|---|---|---|---|---|
| Distribution | `DISTRIBUTION_VERSION` | `assayer 0.1.2` | semver | each package | The pip packaging release of one wheel (`assayer-platform`, `assayer-plugin-sdk`, `assayer-plugin-<domain>`, `assayer-provider-<source>`). Packaging only; it does not by itself define compatibility. |
| Platform API | `PLATFORM_API_VERSION` | `1.0.0` | semver | platform | The semantic version of the generated internal platform surface. The compiler binds it for an ordinary plugin; an Advanced SPI package declares it explicitly. |
| Plugin protocol | `HOST_PROTOCOL_VERSION` | `1.2.0` | semver | SDK + platform | The message/envelope version of the Host-to-plugin protocol (`docs/plugin-protocol-v1.md`). Negotiated before a Run starts. |
| Plugin SDK | `HOST_SDK_VERSION` | `0.1.2` | semver | `assayer-plugin-sdk` | The version of the SDK code and its bundled schemas. Each SDK release declares the protocol range it implements. |

## 3. Relationships

1. **Distribution is not compatibility.** Two different distribution versions
   may be fully compatible. Compatibility is always decided by the plugin's
   declared `platformApiVersion` and `protocol`/`sdk` ranges, never by a pinned
   package version.
2. **SDK tracks the protocol.** An SDK release declares the protocol range it
   implements and carries the schemas for that protocol. `HOST_SDK_VERSION` and
   `HOST_PROTOCOL_VERSION` may advance independently.
3. **Platform API tracks the public surface.** A change that alters the public
   plugin surface (entities, registration, conformance API) bumps
   `PLATFORM_API_VERSION`. Internal kernel, transport, persistence, and
   observability changes do not.
4. **Ordinary authors declare no compatibility ranges.** They declare one
   plugin business version. The compiler derives `platformApiVersion`, the
   protocol/SDK window, Check and domain-contract versions, and package
   compatibility from its own frozen identity. Advanced SPI publishers declare
   the ranges required by their public low-level contract.
5. **Compatibility is negotiated, not pinned.** A plugin MUST NOT depend on a
   platform distribution version range to express compatibility. Depending on
   `assayer>=x,<y` is packaging coupling, not a compatibility contract.

## 4. Compatibility classification (Constitution §5)

For every axis, the change class is:

- **major**: alters lifecycle, safety, evidence, decision states, persistence
  meaning, or another invariant. Requires a new major API and a migration or an
  explicit incompatibility error.
- **minor**: adds backward-compatible optional fields, capabilities, or
  operations. Older consumers continue to read the previous subset.
- **patch**: clarifies wording or fixes an implementation defect without
  changing accepted data or behavior.

## 5. Enforcement

1. Before discovery, the Host runs `negotiate_plugin_compatibility` against the
   generated or Advanced SPI manifest's `compatibility` block.
2. Outside the declared ranges, the Host fails closed with
   `PLUGIN_PROTOCOL_INCOMPATIBLE` or `PLUGIN_SDK_INCOMPATIBLE` and starts no
   Run.
3. A platform-only implementation or transport change MUST remain inside the
   existing platform API and protocol envelope; it MUST NOT require an ordinary
   plugin author-source change (Constitution §3.13-14). The verification is the
   isolation regression: an internal platform change leaves every generated
   plugin release fixture green with zero author-source diff.
