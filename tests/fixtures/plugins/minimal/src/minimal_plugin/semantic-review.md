# Minimal configuration DomainResult

Review the current configuration `agentView` and return only the fields in
`domainContract.resultSchema`. Cite Evidence through `supportedBy` using the
current task's opaque handle. Do not return Run, WorkItem,
contract-digest, task-digest, revision, Decision, or finalization fields.

Use `scanned_no_issue` when the required keys are present, `issue_found` when
they are absent, and `needs_review` when the available Evidence is unresolved.
Include one finding for the `required_keys` dimension.
