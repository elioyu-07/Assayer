# Implementation Slice 090 — Host-Owned Evidence Reference Resolution

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


Status: implemented

This slice closes the Evidence boundary for SDK v2 DomainResults. Agents may
refer to facts, but the Host remains the authority that issued and scoped
those references.

## Delivered

- Added Host-side Evidence reference resolution for an
  `InvestigationPacket`.
- Accepted only stable string references found in the current packet's
  Evidence identity or Host-issued nested source-chunk identifiers.
- Rejected unknown, duplicate, cross-WorkItem, and cross-Run references before
  Decision/ledger mutation.
- Validated both the Agent DomainResult and the plugin's mapped Decision
  projection.
- Canonicalized valid references into `Decision.details.evidenceRefs` while
  keeping the Agent unaware of Run, WorkItem, checkpoint, and digest fields.
- Converted mapper-side reference violations into
  `PLUGIN_CONTRACT_IMPLEMENTATION_MISMATCH`.

## Verification

- Added tests for legal Evidence IDs and nested `source_chunk_id` references.
- Added tests for unknown and duplicate references, including the no-ledger-
  mutation invariant.
- Added tests for mapper cross-packet references and canonical Decision
  details.
- Domain-result test module: 10 tests passing.
- Python compilation and `git diff --check` pass.

## Follow-up boundary work

- Installed release acceptance is enforced by Slice 092.
- Large domain datasets still need domain-stage paging; any future paging must
  remain inside the DomainResult task model and must not expose checkpoint or
  cursor fields to the Agent.
