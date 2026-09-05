# Implementation Slice 077 — Portable Evidence-Graph Contract

Status: implemented

The evidence graph now has a small platform-owned portable projection
validator. Any plugin may publish the additive projection without adopting the
Spec review protocol.

The validator checks required fields, count bounds, unique group/member
identity, and consistency between pending members and the coverage flag. It
does not interpret rule semantics, severity, or domain findings.

Spec validates its own emitted projection at packet and final-review
boundaries. Existing plugins are not forced to emit candidate graphs; adoption
is opt-in and therefore remains backward compatible. The same adapter and
validator can be used by a future plugin without importing Host, MCP, or
browser code.

Verification includes portable-projection failure tests and regression tests
for Spec and the independent plugin conformance suite. The Config Quality
plugin now emits the same projection as a second, non-Spec adoption proof;
its payload contains only configuration observations and generic graph fields.
