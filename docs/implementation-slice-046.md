# Vertical Slice 046: Incremental Semantic Review Checkpoints (M3)

Interactive Runs can now persist semantic-review progress after each bounded
Evidence collection page. `checkpoint_review` binds an opaque plugin payload to
the current WorkItem, Check version, collection, and stable item IDs. The
platform rejects unknown or overlapping IDs, replays identical submissions
idempotently, writes each accepted fragment to the ledger, and reports reviewed
versus remaining item counts.

Final `submit_decisions` calls can reference the small set of checkpoint IDs
plus finalization data. The platform requires those checkpoints to cover every
collection item exactly once and invokes an optional plugin assembly hook. The
assembled details then pass through the unchanged DecisionProposal, semantic
committer, receipt, and terminal gates. Checkpoints cannot publish a conclusion
on their own.

The Spec-quality plugin implements the hook by assembling paged review
decisions and the final readiness context into its canonical `details.review`
envelope. It additionally verifies that every checkpoint payload covers its
declared candidate IDs exactly once. The Agent no longer resends hundreds of
candidate dispositions in one final tool call, while the complete review and
all intermediate fragments remain durable and auditable.

The development bundle `0.1.0+codex.20260903010428` was built with its offline
wheelhouse, validated through the clean launcher check, and installed from the
personal marketplace. This proves package/version alignment only. A new Codex
CLI task must provide the real Agent-facing acceptance evidence; no audit was
started as part of this slice.
