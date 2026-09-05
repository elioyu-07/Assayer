# Layer Convergence L2 — Markdown Navigation Adapter

Status: implemented

The platform now exposes `MarkdownNavigationAdapter` through the generic
`NavigationProvider` seam. It delegates only deterministic parsing and stable
source-unit production to the standalone Markdown capability provider.

Spec now consumes this platform adapter for source facts and navigation maps;
its chapter rules, candidate semantics, and review policy remain in the Spec
plugin. Existing parser output and unit identities are preserved.

The adapter is intentionally stateless and read-only. Source rereads that
need a target are explicit, so later pagination and evidence migration cannot
silently use stale content.
