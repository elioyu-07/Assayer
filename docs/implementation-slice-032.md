# Vertical Slice 032: Real-Path Test Gate (C07.2)

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


Adds scripts/run_tests.py fast and full. Fast runs deterministic tests; full preflights jsonschema, Playwright, MCP SDK, and Chromium, then runs every test and fails on any skip. Test extras pin Playwright 1.62.0 and MCP 1.27.0; CI has separate fast/full jobs.

Full acceptance executes real Chromium and MCP tests with zero skips. Missing dependencies or startup failure fail during preflight. Fast is for development; release and C07 acceptance require full.
