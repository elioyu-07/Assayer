# Spec Quality Standard

Version: 1.3.0

This document defines the normative meaning of `CHK-01` through `CHK-18`.
The checks are semantic content contracts. The default profile does not
require one chapter layout. `spec-template.md` is a recommended authoring aid;
its exact structure is mandatory only under `strict-12-chapter`.

## Decision vocabulary

- `PASS`: sufficient, applicable, internally consistent evidence establishes
  the content contract.
- `REWORK`: the Spec has a remediable content defect, omission, ambiguity, or
  inconsistency.
- `ESCALATE`: an authorized owner must resolve a policy conflict, approve a
  risk decision, or choose between materially different outcomes.
- `UNVERIFIED`: the required source, external fact, version, approval, or
  evidence cannot be established from the audit scope.

An Agent must not invent business facts, target values, approvals, external
behavior, or non-applicability. A keyword or heading is candidate evidence,
not a final conclusion.

## CHK-01: Content completeness

All mandatory quality dimensions must have locatable evidence or an explicit,
justified non-applicability decision. Under the default profile, missing,
renamed, reordered, combined, or split chapters are not defects by themselves.

- `PASS`: every applicable dimension is covered and every non-applicable
  dimension has a supported rationale.
- `REWORK`: mandatory content is missing, empty, placeholder-only, or claims
  non-applicability without a sufficient reason.
- `ESCALATE`: governing sources disagree about required content or structure.
- `UNVERIFIED`: the selected governing profile or required source cannot be
  established.

Exact heading names, numbering, order, uniqueness, and required chapter bodies
belong only to `strict-12-chapter` or another explicitly selected structure
profile.

## CHK-02: Independent comprehensibility

Using only the Spec and authorized sources, an independent reviewer must be
able to reconstruct the goal, actors, trigger, preconditions, inputs and their
sources, core behavior, outputs, failure behavior, state changes, scope, and
acceptance.

Each answer is classified as `EXPLICIT`, `DERIVED`, `MISSING`, `AMBIGUOUS`,
`CONFLICTED`, `UNVERIFIED`, or justified `NOT_APPLICABLE`.

- `PASS`: every material answer is explicit, safely derived, or justified not
  applicable.
- `REWORK`: an answer is missing or materially ambiguous.
- `ESCALATE`: authorized sources conflict or an owner decision is required.
- `UNVERIFIED`: reconstruction depends on unavailable evidence.

## CHK-03: Terms and abbreviations

Business-significant terms, abbreviations, aliases, statuses, roles, and range
or time concepts must have unique, traceable definitions. Common technical
terms need definitions only when the Spec gives them special business meaning.

- `PASS`: material terms are defined once and used consistently, or the Spec
  explicitly establishes that no specialized terms apply.
- `REWORK`: a material term is undefined, multiply defined, or used
  inconsistently.
- `ESCALATE`: authoritative sources assign competing meanings.
- `UNVERIFIED`: the controlling definition source cannot be read or versioned.

## CHK-04: Scope and boundaries

The Spec must explicitly define current in-scope delivery, nearby out-of-scope
capabilities, responsibility boundaries with upstream and downstream parties,
and deferred or phase boundaries. It does not need an infinite list of
everything the system will not do.

- `PASS`: a reviewer can decide whether adjacent work belongs to the current
  delivery and who owns excluded behavior.
- `REWORK`: a material inclusion, exclusion, responsibility, or phase boundary
  is absent or ambiguous.
- `ESCALATE`: owners disagree about scope or responsibility.
- `UNVERIFIED`: the governing delivery baseline is unavailable.

## CHK-05: Semantic consistency

Review the anchor Spec and explicitly related documents for five distinct
conditions: ambiguity, contradiction, conflict, duplicate, and drift. Compare
only the same object under overlapping role, state, phase, region, and
effective-time context. Explicit contextual differences are not conflicts.

Cross-document findings require anchor identity and digest, related-document
identity and digest, relationship, both excerpts, governing authority, impact,
and resolution owner. One root cause must not create repeated findings.

- `PASS`: applicable evidence is consistent and intentional differences are
  explained.
- `REWORK`: the Spec contains a remediable ambiguity, contradiction,
  uncontrolled duplicate, or drift.
- `ESCALATE`: equal or higher authorities conflict and require an owner choice.
- `UNVERIFIED`: related evidence, identity, version, or authority is missing.

## CHK-06: Version and revision history

Metadata and revision evidence must agree on the current version, status,
owner, and baseline. Record substantive revisions affecting behavior, scope,
state, data, permissions, acceptance, NFRs, dependencies, or decisions. Each
such revision includes version, date, responsible person, summary, and
affected stable IDs. Typographical or formatting-only changes need no business
revision unless project governance requires one.

- `PASS`: the current baseline and substantive history are traceable and
  consistent.
- `REWORK`: required metadata or substantive revision data is incomplete or
  inconsistent.
- `ESCALATE`: ownership or baseline authority is disputed.
- `UNVERIFIED`: the claimed version, commit, or revision evidence is
  unavailable.

## CHK-07: Semantic clarity

Material conditions, quantities, time, scope, roles, and outcomes must have one
observable and decidable interpretation. A formal ambiguity finding requires:

1. at least two reasonable interpretations;
2. different implementation, test, or acceptance outcomes; and
3. no discriminator in the authorized evidence.

Not every requirement must be numeric. Keywords are only candidate signals.

- `PASS`: material statements are observable and decidable.
- `REWORK`: the three ambiguity conditions are met or an outcome is
  subjective and undecidable.
- `ESCALATE`: an owner must choose between valid interpretations.
- `UNVERIFIED`: a referenced discriminator or authority cannot be checked.

## CHK-08: Risk-driven scenario coverage

Evaluate applicability of normal, boundary, invalid-input, permission,
dependency-failure, timeout and retry, concurrency and idempotency, recovery,
lifecycle, and batch or partial-success scenarios. Applicable categories need
distinct observable outcomes; inapplicable categories need reasons. Repeated
or padded CASE counts do not prove coverage.

- `PASS`: material risk categories are covered or justified not applicable.
- `REWORK`: an applicable scenario or observable outcome is missing.
- `ESCALATE`: risk ownership or acceptable behavior requires an authorized
  decision.
- `UNVERIFIED`: scenario applicability depends on unavailable system or policy
  evidence.

## CHK-09: Business data contract

Every important field must define a stable name, business meaning, logical
type, source and truth owner, required and null behavior, default, valid range
or values, validation, and invalid-input response. Add applicable unit,
precision, rounding, uniqueness, reference relationship, lifecycle, or formula
for text, identifiers, enums, numbers, money, date and time, booleans, files,
collections, and computed fields. Physical database types do not replace
business semantics.

- `PASS`: each important field has the decisions required by its logical type.
- `REWORK`: an implementer or tester must guess a material field rule.
- `ESCALATE`: owners disagree about source of truth or business meaning.
- `UNVERIFIED`: an asserted external data contract cannot be verified.

## CHK-10: State model and lifecycle

Stateful objects require state meaning, entry and exit, allowed actions, legal
transitions, actors, triggers, guards, preconditions, postconditions, terminal
states, invariants, and applicable illegal, repeated, concurrent, failure, and
recovery behavior. Diagrams, tables, requirements, acceptance criteria, and
cases must agree. Stateless features require an explicit rationale.

- `PASS`: the lifecycle is complete and mutually consistent, or statelessness
  is justified.
- `REWORK`: a material state, transition, guard, invariant, or failure outcome
  is missing or inconsistent.
- `ESCALATE`: the intended lifecycle requires an owner decision.
- `UNVERIFIED`: lifecycle behavior depends on unavailable external evidence.

No particular diagram syntax is mandatory under the default profile.

## CHK-11: Existing-system compatibility

When changing or integrating an existing system, record the system, version,
ownership baseline, existing behavior, intended change, interface, data,
state, permission, and client impact, backward compatibility, migration,
switching, rollback, and verification. A difference may be intentional and is
not automatically a Spec defect.

- `PASS`: in-scope evidence supports the compatibility or intentional-change
  claim and its transition plan.
- `REWORK`: the Spec omits a material compatibility or migration decision it
  can define.
- `ESCALATE`: responsible owners must choose or approve a compatibility break.
- `UNVERIFIED`: existing behavior, version, or verification evidence is
  unavailable.

## CHK-12: Executable acceptance

Each acceptance criterion must be atomic, observable, and independently pass
or fail, and map to meaningful CASEs. A CASE defines preconditions, input,
action, expected result, and evidence. Verification may be automated, manual,
or hybrid. Manual acceptance still needs reproducible steps, evidence, and an
owner. Subjective outcomes and duplicate-case padding are invalid.

- `PASS`: material criteria and cases are reproducible and decidable.
- `REWORK`: acceptance is subjective, compound, missing material inputs or
  outcomes, or not meaningfully covered.
- `ESCALATE`: an owner must decide the acceptance boundary.
- `UNVERIFIED`: required acceptance authority or evidence cannot be checked.

## CHK-13: Non-functional requirement decidability

Evaluate applicable performance, capacity, availability, reliability,
resilience, recovery, observability, and related NFR categories. Every category
must be `ADOPTED`, `NOT_APPLICABLE`, or `EXEMPTED`.

An adopted NFR requires metric, target, conditions or load, environment, time
window, aggregation, verification, evidence, and owner. Non-applicability
requires a reason. Exemption requires risk, compensating control, named
approver, and expiry. An Agent cannot invent targets or approve exemptions.

- `PASS`: applicability and verification decisions are complete and supported.
- `REWORK`: a required decision, measurable contract, or compensating control
  is missing.
- `ESCALATE`: an authorized risk decision or exemption approval is required.
- `UNVERIFIED`: the target, approval, or evidence cannot be established.

Security, privacy, and compliance are not added as a separate nineteenth check
in this version.

## CHK-14: Authorization and data scope

For protected behavior define actor, resource, action, data scope, condition,
allow or deny decision, and denied outcome. Cover applicable read, create,
update, delete, approve, import, export, and sensitive operations. State-based
permissions must agree with the state model. Undefined combinations need a
deterministic default. Non-applicability requires evidence that no identity,
restricted operation, private data, role, or data-scope distinction applies.

- `PASS`: applicable authorization and isolation outcomes are deterministic.
- `REWORK`: a protected combination, scope, denied result, or default is
  missing or inconsistent.
- `ESCALATE`: an owner must decide access or isolation policy.
- `UNVERIFIED`: the governing identity or authorization policy is unavailable.

## CHK-15: Bidirectional traceability

Trace business objectives or other authoritative sources through user stories
when applicable, functional requirements, acceptance criteria, cases, and
success criteria. Governance, compliance, existing-system constraints,
contracts, security, and business goals may be valid sources. Check both
directions for orphaned sources, requirements, acceptance criteria, cases, and
success criteria. IDs alone do not prove semantic correspondence.

- `PASS`: every material item has meaningful upstream and downstream links or
  a justified boundary.
- `REWORK`: a material item is orphaned, mislinked, or linked only by identifier
  without semantic correspondence.
- `ESCALATE`: source authority or ownership is disputed.
- `UNVERIFIED`: a required source or linked artifact cannot be read or
  versioned.

No fixed matrix location is required under the default profile.

## CHK-16: Key decisions

Record material decisions affecting behavior, scope, state, data, permission,
acceptance, NFRs, compatibility, or governance. Each decision requires ID,
question, context, options when material, outcome, rationale, trade-offs,
owner, status, date, affected stable IDs, and applicable supersession evidence.
Statuses are `PROPOSED`, `APPROVED`, `REJECTED`, `SUPERSEDED`, or `EXPIRED`.
Low-level implementation decisions stay out unless they affect external
behavior or governance. Decisions do not replace requirements.

- `PASS`: material decisions are complete, current, and traceable.
- `REWORK`: decision context, rationale, ownership, impact, or supersession is
  incomplete.
- `ESCALATE`: an authorized decision is still required or authorities conflict.
- `UNVERIFIED`: approval, status, or supporting evidence cannot be verified.

## CHK-17: Dependencies and assumptions

Distinguish confirmed dependency facts from assumptions. For each dependency,
record identity and version, owner, purpose, prerequisite, expected behavior,
unavailable, timeout, and error behavior, retry or no-retry decision,
degradation, consistency impact, observability, recovery, and evidence. For
each assumption, record the statement, basis, owner, validity condition or
period, impact if false, and closure or verification plan.

- `PASS`: material dependencies and assumptions are distinguished, complete,
  owned, and supported.
- `REWORK`: a material behavior, owner, impact, or closure plan is missing.
- `ESCALATE`: dependency ownership or accepted fallback requires a decision.
- `UNVERIFIED`: external behavior or an assumption is asserted without
  verifiable evidence.

## CHK-18: Open matters, exceptions, and exemptions

Manage every unresolved question, blocker, exception, and exemption without
requiring a chapter named `Clarifications`. Each record requires a stable ID,
content, context, impact, status, owner, affected IDs, closure or approval
condition, and evidence. Exceptions and exemptions additionally require risk,
compensating controls, approver, and expiry. Valid states include `OPEN`,
`RESOLVED`, `BLOCKED`, `REJECTED`, `SUPERSEDED`, and `EXPIRED`.

- `PASS`: material open matters and deviations are resolved or governed by
  complete, current records.
- `REWORK`: context, impact, owner, deadline, relationship, closure condition,
  risk, or compensating control is missing.
- `ESCALATE`: an authorized resolution or risk acceptance is required.
- `UNVERIFIED`: a claimed resolution, approval, or exemption lacks verifiable
  evidence.

An Agent must not mark an open matter resolved or approve an exception or
exemption on behalf of its owner.
