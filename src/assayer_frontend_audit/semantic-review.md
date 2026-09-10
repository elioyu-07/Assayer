# Frontend audit DomainResult

Review the current frontend investigation and return only the fields declared
by `domainContract.resultSchema`. Never return Run, WorkItem, checkpoint,
digest, revision, Decision, or finalization fields.

Resolve every reported dimension as `satisfied`, `violated`, `unresolved`,
`blocked`, or `conflicted`. Cite current-task opaque handles in `supportedBy`
when a finding relies on captured Evidence. Choose the overall
result from the contract enum and provide a concise evidence-backed reason.
