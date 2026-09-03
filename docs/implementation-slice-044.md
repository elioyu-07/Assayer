# Vertical Slice 044: Summary-First Collection Paging (M3)

Agent-facing `inspect_work_items` now omits full Evidence payloads by default.
It returns dimensions, metadata, a stable Evidence index, and an Evidence
collection index. A caller can still request compatibility behavior with
`includeEvidence=true` or expand selected immutable Evidence in full.

Plugins can declaratively expose a large array inside Evidence through
`InvestigationPacket.metadata.evidenceCollections`. The platform validates the
Evidence reference, JSON pointer, stable unique item IDs, and declared grouping
fields. `expand_evidence_collection` then provides bounded pages (20 items by
default, capped at 100), cursor binding, mechanical group summaries, and the
original item IDs. Complete Evidence remains unchanged in the Host ledger.

The Spec-quality plugin uses this generic contract for `candidateFindings` and
declares `rule_id` as a mechanical grouping field. Its Skill now reads every
group incrementally and keeps semantic suppression or root-cause merging with
the Agent. No platform branch knows Spec candidate semantics, and no fixed
overall time limit was introduced.
