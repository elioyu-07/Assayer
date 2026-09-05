# Shared Asset Model Contract

Document purpose: this global shared contract defines stable asset concepts,
identifiers, and invariants used by child feature Specs.

| Metadata | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Asset platform |
| Status | Approved |

## Responsibility Boundary

This contract owns the shared asset identity and lifecycle invariants.
Executable workflows, feature-level acceptance criteria, and CASE scenarios are
delegated to child Specs B00 and B01. Normal, boundary, invalid-input,
permission, timeout, retry, concurrency, and recovery scenarios are therefore
not applicable to this shared contract and remain owned by those child Specs.

Out of Scope: user-interface flows and feature-specific service orchestration.

## Stable Requirements

- FR-G01: Every asset has one immutable asset identifier.
- FR-G02: Every asset has exactly one current lifecycle state.
- FR-G03: A lifecycle change records its effective time.
- FR-G04: A lifecycle change records its initiating actor.
- FR-G05: Deleted assets retain their immutable identifier.
- FR-G06: Child Specs reference the shared lifecycle state names verbatim.
- FR-G07: Child Specs preserve the shared identifier across integrations.
- FR-G08: Conflicting child definitions defer to this approved contract.

## Dependencies and Assumptions

- AS-006: No historical data migration is required because the system starts
  with zero asset records.
- Child Specs B00 and B01 are authoritative for their executable feature
  acceptance and scenario evidence.

## Revision History

Version 1.0.0 established the approved shared baseline on 2026-09-05.
