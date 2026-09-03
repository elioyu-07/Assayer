# Vertical Slice 038: Plain-Language Audit Results (J05)

The terminal product summary and `audit-summary.md` now lead with status,
conclusion validity, the concrete terminal reason, and an actionable next step.
They distinguish completed remediation, bounded partial coverage, and failed
Runs whose conclusions cannot be used. The Markdown result also lists every
object decision, issue, needs-review blocker, visited page, rule coverage,
remaining entrypoint, explicit skip, and technical trace.

`needs_review` presentation names the missing fact, unresolved dimensions,
evidence and restored Case counts, and the condition for a new formal decision.
Failed Runs can derive a terminal explanation from the latest failed Host
Operation, and terminal-only failures are attributed from their failure code
instead of appearing as an unexplained empty failure list. Sensitive assignment
patterns remain redacted in both the public terminal summary and human-readable
artifacts.

Deterministic fixtures cover completed/no-issue, completed/issue, partial,
failed, and needs-review presentation. Real Codex CLI acceptance remains an
explicit owner-run backlog item and is not implied by these tests.
