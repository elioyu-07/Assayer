# Historical Document-Review Walkthrough

> This file is retained as a historical product note. The earlier external
> document-review distribution and its runtime instructions are no longer a
> supported Assayer path.

Current ordinary-plugin development is domain-neutral and declaration-only:

```text
plugin.yaml
checks.yaml
semantic-review.md
cases/
        |
        v
compiled-plugin.json
```

Use [Platform Constitution v2](platform-constitution-v2.md) and the
[Plugin Development Guide](plugin-development.md) for all current work. A
Provider supplies source parsing and complete element enumeration; the Host
owns review planning, Evidence, Coverage, Ledger, Result, lifecycle, and
release. No ordinary plugin entry point, parser, or Python runtime is supported.
