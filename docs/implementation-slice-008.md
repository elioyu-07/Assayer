# Vertical Slice 008: Atomic Formal Decision Commit

`commit_decision` reloads PendingDecision and revalidates identity, frozen-rule digest, coverage, Evidence integrity, Case recovery, and IssueScreenshot. One SQLite transaction writes immutable RuleAssessment and a one-to-one Issue for `issue_found`, updates object assessment references, and marks PendingDecision committed. Duplicate object-rule Assessment is rejected or returned idempotently. Any validation failure writes nothing and does not increment revision; invalid recovery invalidates PendingDecision. Evidence digests, Screenshot bytes, and schemas are checked again before commit.

`complete_audit`, full Coverage Universe closure, ledger export, and report derivation belong to Slice 009; real browser adapters remain fail-closed.
