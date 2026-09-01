# Vertical Slice 029: Remove Deterministic Semantics from Formal Path (C06)

C06 removes production RuleEvaluationEngine, Fua10RuntimeEvaluator, and BrowserHostRuntime.audit. Real smoke now collects Host browser facts only and emits mode=host_smoke, publishable=false, zero Assessment/Issue, and no audit ledger. It never calls Finding, decision, or completion tools.

Deterministic Harness remains an explicit test fixture for Host transactions. Production source has no rule-ID decision branches; CLI, README, architecture, and browser tests enforce that smoke cannot impersonate Agent Assessment.
