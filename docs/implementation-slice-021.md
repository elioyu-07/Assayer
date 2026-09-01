# Vertical Slice 021: Real URL Runtime Closure (B10)

B10 assembled real Chromium, Page/Identity/Action/Recovery/Evidence adapters and URL-only entrypoints. start_audit supports anonymous or credential mode; BrowserHostRuntime binds one Session, Scan, URL, output directory, profile, adapter bundle, and HostCore. audit, serve, JSON stdio, and MCP share the runtime; WebSocket remains blocked and hash routes strip query and fragment.

Acceptance covered anonymous startup, inspection, object binding, structured and visual Evidence, bounded navigation, cleanup on exit and failure, and lease-mock smoke. B07c remains deferred, so Raw Visual uses sanitizationStatus=not_performed and cannot support formal issue_found. No lease-specific labels, routes, or selectors are embedded.
