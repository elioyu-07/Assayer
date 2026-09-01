# Vertical Slice 019: General JSON CLI and MCP Transport (B08)

Adds JSON Lines Host transport (one complete request/response per line; malformed lines do not terminate later requests), `assayer-json --stdio`, and optional SDK stdio `assayer-mcp`. Transports only frame, validate, map errors, and delegate to one `HostCore.handle`; they do not create IDs, read browsers, decide rules, or write reports. MCP tool names come from the Host catalog and must match the envelope tool. Responses expose the same Host result as structured content and text. Errors never echo credentials, raw requests, stack traces, or internal exception details.

Tests cover malformed-line isolation, MCP/CLI delegation equivalence, tool mismatch, unknown tools, incomplete envelopes, secret/stack redaction, and preservation of B07c/recovery/issue gates.
