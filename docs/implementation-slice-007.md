# Vertical Slice 007: Decision Preparation and Independent Issue Screenshots

Adds persistent PendingDecision and `prepare_decision`. Host validates object identity, frozen rule, Evidence/Case closure, and coverage rather than trusting Agent claims. `issue_found` requires captured Raw Visual; Host derives a new independent `kind=issue` Screenshot and file. `scanned_no_issue` requires every registry minimum dimension covered by restored Cases. `needs_review` carries a structured blocker; all results retain reasons and references. Preparation increments revision and idempotent retry returns the same PendingDecision/screenshot without writing Assessment/Issue.

`commit_decision`, formal Assessment/Issue writes, object status, and final revision checks belong to Slice 008.
