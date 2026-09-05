# Implementation Slice 080 — Evidence-Graph Result View

Status: implemented

Interactive terminal results now include the optional `evidenceGraph` summary
alongside result overview, decisions, review items, and failures. The summary
is staged through the existing bounded result delivery mechanism, so large
graphs are paged rather than injected into one Agent response.

Terminal-result hydration uses the same field set, preserving replay
identity across transport restarts. The canonical result remains the durable
source; the interactive response is only a summary-first view.

No conclusion, Finding, or coverage state is inferred by the presentation
layer.
