# Vertical Slice 015: Pre-Send Network Interception and Safe Browser Actions

Adds `BrowserNetworkGuard` before `new_page()`, optional WebSocket routing, and `BrowserSafeActionAdapter` over Host locator handles and allowlisted actions. Same-origin static/document and read-only GET/HEAD/OPTIONS may pass; writes, cross-origin, mutations, multipart, Beacon, SSE, WebSocket, and unattributed requests block or become unknown. Persist only sanitized method, safe URL, transport, sent state, and classification. Post-action probes refresh locator registry; lost or changed identity yields `result_unknown`.

Route installation precedes first request; unknown attribution or pre-interception sends cannot become success. Real HTTP tests prove POST never arrives. Full inverse/replay and nine-dimensional recovery belong to B06; MCP transport belongs to B08.
