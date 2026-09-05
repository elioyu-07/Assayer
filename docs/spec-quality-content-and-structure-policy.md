# Spec Quality Content and Structure Policy

Status: approved design direction. Implementation is tracked separately.

## Decision

Assayer evaluates Spec quality by semantic content, not by one mandatory
chapter layout.

The policy has three layers:

1. The eighteen quality dimensions are mandatory content contracts.
2. The bundled twelve-chapter template is a recommended authoring aid.
3. Exact heading, numbering, order, and uniqueness checks are enabled only by
   an explicitly selected strict structure profile.

## Default behavior

The default `product-spec` profile accepts any document structure. A document
does not fail merely because it renames, reorders, combines, or splits
chapters. The review must instead determine whether the required content is:

- present and applicable;
- independently understandable;
- uniquely locatable in the reviewed evidence;
- internally and externally consistent within the declared scope;
- traceable to requirements, acceptance evidence, decisions, and sources; and
- explicit about missing evidence, unresolved matters, and non-applicability.

Missing mandatory content is `REWORK`. A material claim that cannot be
verified from the available authority or evidence is `UNVERIFIED`. A conflict
that requires an authorized owner is `ESCALATE`. Layout differences alone do
not produce a finding.

## Recommended template

`spec-template.md` remains the bundled authoring template. It gives new
authors a coherent twelve-chapter starting point and stable places for the
eighteen quality dimensions. It is not the default audit gate.

Projects may use other templates, multi-document Specs, RFCs, PRDs, or
organization-specific layouts. Equivalent evidence is accepted regardless of
its heading label or location.

## Strict structure profile

The explicit `strict-12-chapter` profile applies the bundled template as a
structural contract. It additionally requires the twelve top-level chapter
names, numbering, order, uniqueness, and meaningful content defined by the
template. The profile must be selected by the user or governing project
policy; Assayer must not infer it from document appearance.

## Evidence and semantic mapping

For every dimension, the reviewer must cite the source file and a bounded
source location or excerpt. A heading keyword is only candidate evidence. It
does not prove that the underlying content is complete or correct.

Content may be distributed across authorized related documents. Cross-document
evidence must retain document identity and digest, relationship to the anchor
Spec, relevant excerpts, authority, impact, and resolution ownership.

If equivalent content cannot be mapped confidently, the dimension remains
`UNVERIFIED`; the reviewer must not convert uncertainty into either a pass or
a defect.

The implementation exposes two always-required review collections, one
conditional relationship review collection, two always-available pageable
reference collections, and one conditional cross-document reference
collection:

- `candidate-findings` requires an explicit disposition for every scanner
  candidate before later semantic stages;
- `cross-document-relationships` is present only for an explicitly declared
  anchor comparison. Every declared relationship receives exactly one
  `COMPATIBLE`, `FINDING`, or `UNVERIFIED` semantic result before checklist
  review can begin;

- `checklist-dimensions` groups `CHK-01` through `CHK-18` into four bounded
  semantic batches. Each accepted batch is durably checkpointed before the
  next batch is offered;

- `dimension-evidence` groups heuristic source mappings by `CHK-01` through
  `CHK-18`;
- `source-sections` preserves every bounded Markdown source chunk with path,
  digest, heading path, and line range.
- `cross-document-evidence` is present only for an explicitly declared anchor
  comparison. It groups immutable excerpts by relationship and document. Each
  item retains the relationship basis, resolution owner, document identity,
  path, digest, source chunk ID, line range, and exact excerpt.

The reference collections are reading context, not review queues. They never
satisfy a dimension automatically and do not contribute to checkpoint
coverage. The product MCP surface exposes their existing bounded read-only
paging operation, while all checkpoint writes and workflow transitions remain
inside `advance_plugin_run`. Finalization is unavailable until every scanner
candidate, every declared relationship, and every CHK has an accepted result.

## Cross-document scope contract

A normal `files` scope continues to treat every supplied file as an independent
Spec WorkItem. A cross-document Run instead declares exactly one `anchor`, one
or more `relatedDocuments`, and one or more directional `relationships` from
the anchor to those related documents. Every document has an explicit ID and
path. Every relationship has an ID, kind, non-empty relationship basis, and
resolution owner; an optional selection reason records why a document or
relationship entered scope.

The plugin resolves paths and computes source digests internally. The user and
Agent do not supply digests or source chunk identities. Duplicate document or
relationship IDs, duplicate paths, unknown relationship targets, reverse or
unanchored relationships, and related documents without an anchor relationship
are rejected before discovery becomes durable.

Only the anchor becomes a WorkItem. Related documents remain bounded Evidence
for that WorkItem and never become implicit independent audit targets. The
combined document-state digest invalidates inspection if either the anchor or a
related document changes after discovery. `scope.schema.json` defines the
business input, while `cross-document-evidence.schema.json` defines the
portable evidence packet. The Agent still owns semantic compatibility,
conflict, ambiguity, and drift decisions. The Host makes that ownership
unavoidable by placing the relationship queue after candidate review and
before checklist review. Each relationship checkpoint is durably validated
against its declared bilateral Evidence; cross-document Decisions cannot be
injected later during finalization.

At each checklist checkpoint, review schema `1.1.0` and later requires every decision to
carry at least one immutable source reference consisting of source chunk ID,
document path, source digest, start line, and end line. The Host rejects
unknown chunks, path or digest drift, and line ranges outside the referenced
chunk before writing the checkpoint. Finalization assembles the accepted
batches and does not accept a second copy of `checklist_review`. Review schema
`1.0.0` remains readable for historical results. New checkpoint-assembled
reviews emit schema `1.3.0`; missing v1.3 semantic fields are rejected rather
than downgraded to a legacy envelope. The schema also admits cross-document reviewer-origin
findings only when they identify one declared relationship, exactly both
relationship documents, one affected CHK, affected semantic elements, the
declared resolution owner, a next action, and exact frozen evidence references
from each side. The Host rejects one-sided or identity-mismatched claims before
commit. `unverified_dependency` remains `UNVERIFIED`; deterministic validation
does not upgrade it into a confirmed semantic problem.

## Non-applicability

An inapplicable content dimension requires an explicit rationale and evidence
appropriate to its risk. The rationale may appear anywhere that is uniquely
locatable. The default profile does not require an empty placeholder chapter.

## Compatibility boundary

- `product-spec`: default, content-first, layout-neutral.
- `strict-12-chapter`: explicit, content-first plus exact bundled structure.
- `speckit`: explicit lifecycle and artifact profile.
- `adversarial`: explicit semantic review overlay retained for compatibility.

Changing the default profile does not weaken any of the eighteen content
standards. It removes formatting-only failures and moves final judgment to
evidence-backed semantic review.
