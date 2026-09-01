# Vertical Slice 037: CLI Runtime Experience and Performance Baseline (C07.3-06 / J04)

The first real CLI sample took 643.5 seconds while Host Operations totaled 6.573 seconds; most delay was Agent waiting. It completed three FUA-10 no-issue decisions across five PageStates and 28 entrypoints, but emitted no intermediate progress and no public decision reasons. J02/J03 passed; J04 failed.

Fixes require bounded 1-480 character decisionReason, optional model telemetry, stage progress, and a new-session rerun. Product MCP then internalized protocol fields, fixed outputDir=auto, browserProfile=default, authMode=anonymous, and atomic decision/coverage APIs. J04 still requires three consecutive CLI runs with progress, diagnostic partial/failed UX, and no stale-state reuse.
