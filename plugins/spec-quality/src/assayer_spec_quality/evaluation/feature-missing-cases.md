# Order Export Feature Spec

Document purpose: this feature Spec owns the user-triggered order export flow.

| Metadata | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Order operations |
| Status | Proposed |

## Scope

The feature exports a selected order as a CSV file.
Out of Scope: scheduled exports and multi-order batch exports.

## Functional Requirement

FR-F01: Given an authorized operator and an existing order, the system exports
that order after the operator selects Export.

AC-F01: Given order `ORD-001`, when an authorized operator selects Export,
then the response downloads one CSV file whose order identifier is `ORD-001`.

## Revision History

Version 1.0.0 introduced FR-F01 and AC-F01 on 2026-09-05.
