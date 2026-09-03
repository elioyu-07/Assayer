# Vertical Slice 043: Layered Investigation Evidence (M3 foundation)

The domain-neutral interactive controller can now return an InvestigationPacket
without embedding every Evidence payload. The response keeps dimensions and a
stable Evidence index; `expand_investigation` returns selected immutable
Evidence by WorkItem and Evidence ID. Full Evidence remains available for
backward-compatible callers through `includeEvidence=true`.

This is a transport/context optimization, not semantic compression. The Host
stores and validates the complete packet, evidence references, source identity,
and recovery state before any decision. Summary-first defaults, plugin/Skill
guidance, and nested collection paging are delivered by Vertical Slice 044.
