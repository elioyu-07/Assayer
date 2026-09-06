# Layer Convergence L2 — Markdown Navigation Adapter (removed)

Status: superseded

The original L2 slice exposed `MarkdownNavigationAdapter` through the generic
`NavigationProvider` seam, delegating parsing to the standalone Markdown
capability provider.

This was removed because it violated the Platform--Plugin Boundary Contract:
the platform must be the neutral hub and must not consume the pluggable
capability layer. A platform-owned navigation adapter that routes into a
provider made the platform a capability consumer and embedded Markdown domain
semantics into the platform.

Navigation facts are now produced by the platform's own domain-neutral source
chunking (`source_chunking.py`), which performs heading-aware splitting without
importing any provider. The Markdown document-navigation capability remains an
installable provider for plugins that need it.
