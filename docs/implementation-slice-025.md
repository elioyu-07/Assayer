# Vertical Slice 025: Engineering Rename to Assayer

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


The product and engineering namespace moved to Assayer: Python package assayer, modules assayer_host, CLIs assayer/assayer-harness/assayer-json/assayer-mcp, schema namespace https://assayer.dev/schemas/..., and repository elioyu-07/Assayer. Rule IDs, protocol fields, ledger entities, slice numbers, and audit semantics remain unchanged.

This was an intentional early breaking rename. Legacy imports and CLI aliases are not retained. Editable distribution, four CLI entrypoints, schemas, links, and lease smoke were verified.
