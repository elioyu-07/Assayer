# Vertical Slice 028: Dynamic MCP and Formal Product Entrypoint (C05)

C05 adds RuntimeRouter, fixed output root, per-Scan runtimes, concurrency and lease limits, URL binding at start_audit, and shared CLI/Desktop MCP configuration. outputDir=auto is the only formal product value; absolute paths and traversal are rejected. Lease expiry, EOF, Agent exit, or Router close fails active Scans and invalidates active work.

assayer audit launches Codex with dynamic stdio MCP and never silently substitutes smoke. serve, JSON, and MCP share Router. C06 later removes the deterministic semantic path temporarily retained by C05.
