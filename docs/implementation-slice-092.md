# Implementation Slice 092 — Enforce Installed DomainResult Acceptance

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


Status: implemented

This slice closes a release-gate hole left by the SDK v2 migration. Installed
interactive plugins are now accepted only after their packaged DomainResult
journey runs through the platform-owned transport.

## Delivered

- Switched installed semantic-instruction verification from legacy
  `AgentContractBundle` registrations to `DomainResultContract` registrations.
- Made every installed DomainResult plugin declare and execute a packaged
  `releaseAcceptance` driver.
- Restricted the acceptance transport to `domainResult` semantic submission;
  it no longer offers checkpoint preflight, checkpoint mutation, direct
  Decision, or closeout inputs.
- Replaced checkpoint-page and collection claims with the v2 metrics
  `domainResultSubmissions` and `agentCorrections`.
- Cross-checks driver claims against Host-observed submissions and durable
  terminal Decision records.
- Migrated the independent `ass-spec` installed and external acceptance
  outputs to the v2 acceptance schema.

## Failure behavior

- Missing installed semantic instructions fail before the acceptance driver.
- A missing mapper or runtime contract mismatch fails the installed stage.
- A driver cannot claim extra DomainResult submissions, resume, replay, or
  result publication that the Host did not observe.
- There is no legacy acceptance fallback.

## Verification

- DomainResult release-gate fixtures pass for the valid installed wheel.
- Malformed schemas, missing mappers, invented metrics, missing replay, and
  missing semantic resources fail at deterministic gates.
- The independent `ass-spec` DomainResult tests and external journey pass.
