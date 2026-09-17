# Assayer Design Confirmation Contract v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-15 |
| Status | Active development gate |
| Owner | Assayer maintainers |
| Authority | Platform Constitution v2 |

## 1. Purpose

A Design Confirmation is the machine-readable boundary record that must exist
before an Agent writes implementation code. It converts an intended change into
an auditable ownership, capability, dependency, compatibility, and acceptance
decision.

The record is not a plan or a retrospective explanation. It is a precondition
for implementation. A missing, invalid, pending, rejected, or superseded
record blocks the change.

## 2. Repository location and format

Each change has one versioned JSON record under:

```text
design/changes/<change-id>.json
```

The record MUST validate against
[`design-confirmation.schema.json`](../schemas/design-confirmation.schema.json).
Natural-language discussion may surround the record, but the record is the
only machine authority for the preflight gate.

## 3. Required decisions

The record MUST identify:

- the change type and immutable change ID;
- the repository paths covered by the change;
- the authoritative Constitution and lower contracts;
- the intended platform, Provider, Agent, SDK, and plugin ownership;
- required input kinds and capabilities;
- forbidden dependencies and responsibilities;
- public-contract and historical-result impact;
- acceptance gates that prove the change; and
- human approval when the change affects a public boundary, semantic meaning,
  safety, persistence, or release behavior.

The ownership split MUST be explicit. “Shared”, “temporary”, “compatibility”,
or “the implementation will decide” are not valid owners.

## 4. Preflight states

```text
proposed -> machine_checked -> approved -> implemented -> verified
                      \-> rejected
```

`machine_checked` proves structure, references, allowed change type, and
required gates. `approved` proves that the responsible human accepted the
design direction. `implemented` and `verified` are written by the development
and release gates, never by the Agent's initial authoring step.

Implementation is permitted only for an `approved` record. A public-contract,
semantic, safety, persistence, or release change always requires human
approval. A purely internal implementation change may use the `internal_only`
impact classification, but the machine gate must prove that classification and
the absence of public impact.

## 5. Immutability and supersession

Once implementation begins, the approved record is immutable. A changed scope,
ownership split, capability, forbidden dependency, compatibility classification,
or acceptance gate requires a new change ID and a new approval. A superseded
record remains in history and cannot authorize new code.

## 6. Required acceptance

Every record names concrete gates. Depending on the change type, these include
source boundary, document vocabulary, compiled contract consistency, Provider
conformance, exhaustive ReviewBatch coverage, exact compiled-artifact
verification, installed lifecycle, replay/recovery, and canonical result
checks. A record cannot claim completion by referring only to unit tests or
manual inspection.

## 7. Enforcement

The Agent preflight command rejects any record that is missing, malformed,
unapproved, stale, contradictory, or incomplete. CI repeats the check and
rejects a commit or merge whose changed paths are not covered by an approved
record. Release tooling checks the same record before accepting an artifact.

`changedPaths` uses repository-relative paths and may use a trailing `*` glob
for a deliberately bounded directory. A record must not claim the repository
root or an unbounded workspace as its scope.
