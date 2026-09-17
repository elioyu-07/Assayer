# Implementation Slice 094 — Remove Legacy SDK Surface

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


Status: implemented

The removed AgentContract protocol is no longer a public SDK capability.

## Delivered

- Removed `AgentContractBundle` and its legacy constants from the top-level
  `assayer_platform` public SDK exports.
- Removed it from the plugin public-surface whitelist.
- `PluginRegistration` rejects any non-empty legacy Agent contract list with
  `PLUGIN_LEGACY_CONTRACT_UNSUPPORTED`.
- A stale installed package that still imports the removed SDK surface is
  quarantined as `dirty` during registry discovery instead of crashing Host
  startup or being silently adapted.
- Removed obsolete AgentContract/checkpoint test modules.

## Verification

```text
Ran 753 tests
OK (skipped=36)
```

The remaining skipped tests are mixed historical ass-spec cases and optional
MCP dependency checks; no public compatibility path was restored.
