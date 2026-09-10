# Implementation Slice 093 — Remove Obsolete Legacy Test Surface

Status: implemented

The SDK v2 boundary is now the only active Agent-facing contract. Tests that
only exercised the removed `AgentContractBundle` checkpoint protocol were
deleted from the active test tree instead of being left as compatibility
claims.

## Delivered

- Removed the obsolete AgentContract model/conformance test module.
- Removed the obsolete Host checkpoint-boundary test module.
- Retained DomainResult contract, evidence-reference, mapper, retry-budget,
  resume, terminal replay, MCP surface, and installed release-gate tests.
- Reduced the main test suite from 59 explicit skips to 36.

## Remaining skips

- The remaining Assayer skips are only legacy scenarios still embedded in the
  mixed ass-spec historical test module and the optional MCP dependency gate.
- They do not expose a runtime compatibility path and do not count as passing
  SDK v2 behavior.
- They are the next cleanup slice: rewrite each useful invariant against
  DomainResult, then delete the obsolete scenario rather than restoring the
  checkpoint protocol.

## Verification

```text
Ran 753 tests
OK (skipped=36)
```

DomainResult/release-gate focused tests: 56 passed.
