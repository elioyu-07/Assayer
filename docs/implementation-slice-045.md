# Vertical Slice 045: Safe Adaptive Inspection Batches (M3)

The platform now treats failed-batch retry as an explicit plugin capability.
The optional manifest field `failureSplitting` defaults to `forbidden` and is
effective only when inspection batching is allowed and WorkItem ordering is
independent. This prevents the previous batch kernel behavior from replaying a
strict, ordered, or potentially side-effecting inspection without permission.

For opted-in plugins, both batch and interactive execution recursively split a
failed inspection batch. Successful sub-batches retain their complete packet
validation and ledger checkpoints; an isolated failure is attached only to its
WorkItem. The largest successful sub-batch becomes the conservative size for
later WorkItems in the same Run. There is no retry count or elapsed-time cutoff:
splitting is structurally bounded because each retry contains fewer WorkItems.

Interactive Runs persist split events, isolated failures, attempt counts, and
the effective batch size. Agent responses expose the same diagnostics. Plugins
that do not opt in receive one failed attempt, no automatic replay, and an
explicit failure for every WorkItem affected by the unresolved batch.
