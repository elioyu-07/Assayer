# Vertical Slice 012: BrowserSession and Configuration Boundaries

Adds constrained `BrowserProfile` (Chromium, headless, viewport, locale, timezone, bounded waits) and `BrowserSession` (one Context per Scan, explicit lifecycle, failure invalidation). `run_serial` serializes callbacks under a Session lock and stops using an untrusted Context after callback failure. `ScanSessionRegistry` rejects duplicate registration, releases only terminal Scans, and closes each session once.

`BrowserBackend` is the only future Playwright injection point. Adapters cannot hold global Pages or write launch parameters, cookies, proxy secrets, or ElementHandles to Core, ledger, or MCP. Configuration, missing backend, callback failure, concurrency, duplicate registration, and release behavior are covered by tests.
