# Assayer Evidence, Decision, and Ledger Integrity

| Metadata | Value |
|---|---|
| Document version | 1.1.0-draft |
| Date | 2026-08-31 |
| Status | Design converging |
| Owner | Host Core / Audit Owner |

## 1. Evidence Layers

These layers cannot impersonate one another:

1. **Raw Fact**: source material Host reads from browser, network, or source and holds temporarily;
2. **Evidence**: immutable record after trimming, sanitization, normalized summarization, and entity binding;
3. **Agent Evidence Pack**: minimum sufficient pack containing only existing Evidence references and fact summaries;
4. **Agent Finding**: immutable structured Agent interpretation of one rule coverage dimension based on Evidence; it is neither a Host Fact nor a final conclusion;
5. **Derived View**: Assessment, Issue, report, and diagnostic views derived from ledger facts.

Agent references Evidence IDs and cannot copy a fact and redeclare it as Host evidence. Host cannot place Agent natural-language rationale in an Evidence payload.

DimensionFinding preserves Agent provenance and Evidence/Case references. It cannot modify Evidence or use natural-language rationale to rewrite `blocked`, `ambiguous`, or `restore_failed` Host facts as success.

## 2. Evidence Binding

Every Evidence record binds:

- `scanId`;
- `pageStateRef`;
- `objectRef`;
- optional `caseRef`;
- `capturedAt` and capture revision;
- `kind`, collector version, and sanitization-policy version;
- normalized payload;
- `integrityDigest`.

Material without unique object and PageState ownership may be diagnostic only and cannot support a formal decision. Source evidence requires a page -> route -> component/handler/API ownership chain; when the chain does not close, use `sourceBinding.status=unverified`.

## 3. Normalization and Digests

All digest algorithms use UTF-8 and deterministic JSON serialization: object keys sorted by Unicode code point, arrays retaining semantic order, and insignificant whitespace removed. Binary data hashes original bytes. The algorithm description and version enter the ledger.

- Evidence `integrityDigest` hashes the normalized Evidence envelope without write time, random IDs, file paths, and other non-factual fields;
- PageState `domDigest` hashes Host-selected normalized state material, not the complete DOM;
- Rule content digest hashes normalized bytes for the rule file, registry entry, and referenced dependencies;
- Screenshot digest hashes final immutable image bytes;
- If any digest cannot be computed, no placeholder may enter the formal ledger.

## 4. Visual Evidence and Screenshots

### 4.1 Raw Visual Capture

Captured during Case execution when required by a rule and bound to the current PageState, Object, and Case. It may be a complete-object image, crop, or screenshot metadata and does not automatically become a formal issue screenshot.

### 4.2 IssueScreenshot

A formal issue screenshot is derived from a Raw Visual at the same issue state:

- Reconfirm that the object is uniquely locatable;
- Record the defect bounding box;
- Irreversibly mask sensitive regions;
- Store image bytes, dimensions, type, digest, source Raw Visual reference, and capture revision;
- One Issue references one independent IssueScreenshot; multiple Issues on one object do not reuse an unmarked generic image.

When capture fails, the object is `ambiguous`, or sanitization fails, `issue_found` cannot be created.

## 5. Decision Transaction

Formal decisions use a two-phase transaction after the recovery barrier:

```text
Agent/Host -> restore_case
Host       -> only restored Cases may call prepare_decision
Agent      -> prepare_decision
Host       -> validate rule, final Findings, evidence, and semantic fields; create PendingDecision and IssueScreenshot
Host       -> only restored state may call commit_decision
Host       -> atomically write RuleAssessment and derive Issue when needed
```

`prepare_decision` cannot change formal Assessment or Issue arrays. `commit_decision` performs one transaction:

1. Revalidate Scan terminal state, object identity, registry digest, and runRevision;
2. Validate closure of all Finding, Evidence, Case, and Screenshot references;
3. Validate consistency among result, `applicable`, resolved/unresolved coverage, and field gates;
4. Write immutable RuleAssessment;
5. For `issue_found`, write one Issue linked one-to-one to its Assessment;
6. Increment runRevision and record the commit Operation.

Any failed check rolls back the whole transaction. PendingDecision remains as `rejected` or `invalidated` diagnostics.

## 6. Hard Result Gates

| Result | Gate |
|---|---|
| `issue_found` | Rule enabled, object matched, coverage satisfied, evidence valid, Cases restored, IssueScreenshot captured, semantic fields complete. |
| `scanned_no_issue` | Rule enabled, object matched, final Finding for every minimum dimension satisfied, referenced Cases restored, no unresolved or conflicting evidence. |
| `not_applicable` | Applicability evidence and rationale exist for this object; a capability gap cannot impersonate non-applicability. |
| `needs_review` | Evidence gap, conflict, blocker, or identity issue is explicit; completed coverage is not claimed. |
| `noise` | Candidate genuinely relates to the object but rule semantics confirm it is not an issue; noise rationale is recorded. |

## 7. Conclusion Invalidation Propagation

Committed Assessments and Issues are immutable. Invalidation adds an event or hides them in derived views; it never deletes history. Conclusions become invalid when:

- Scan enters `failed`;
- A possibly sent unknown write request is discovered;
- Ledger integrity validation fails;
- Rule or identity-algorithm digests differ from frozen versions;
- Evidence, Screenshot, or Object is proven misbound;
- Page contamination expands beyond object isolation.

Invalidation is recorded in `conclusionValidity` and `invalidatedBy`. `partial` retains only conclusions that passed recovery and are not covered by invalidation events.

## 8. Agent Evidence Pack Trimming

Host supplies by default only:

- Current object and PageState summary;
- Evidence kinds and IDs required by the rule;
- Before/after state directly related to the current Case;
- Minimum source snippets and ownership chain;
- Sanitized request summary;
- Current coverage progress and gaps.

Complete page DOM, complete source repository, all historical screenshots, and credential-related state never enter model context. Every evidence pack records generation version and `runRevision`; stale evidence cannot support an action or commit.

## 9. Ledger Integrity Validation

At each commit and before `complete_audit`, Host validates:

1. Every entity ID is unique within the Scan;
2. Every reference exists, belongs to the same Scan, and has the correct type;
3. Frozen rules match Registry content and digest;
4. PageState/Object/Case/Evidence/DimensionFinding/Screenshot/Assessment/Issue relationships close;
5. Issue links one-to-one to an `issue_found` Assessment;
6. Case recovery and PendingDecision barrier satisfy the result gates;
7. Sensitive-data scan and digest validation pass;
8. Historical events replay to the current state and runRevision.

Host Core currently implements structured Evidence and Raw Visual binding, sanitization, digests, immutable writes, and `prepare_decision` PendingDecision binding. If screenshot sanitization is unconfirmed, the object is unlocated or ambiguous, or file bytes conflict, Host records a failure fact that cannot satisfy the formal screenshot gate. The decision-preparation transaction copies a same-Raw-Visual `kind=issue` screenshot into an independent file and entity. `commit_decision` remains responsible for the atomic Assessment/Issue write.

Implementation status, 2026-08-30: B07a real structured Evidence, B07b real object-level screenshots, B08 JSON/MCP transport, and B09 release-level fault injection are complete. Raw Visual image binding, cropping, digest, immutable persistence, and derivation were verified. B07c automatic pixel sanitization is not implemented, so screenshots use `sanitizationStatus=not_performed`, cannot claim `sanitized`, and cannot enter formal `issue_found`. B08/B09 cannot relax this restriction.

Implementation status, 2026-08-31: C02 implements DimensionFinding schema, Store, tools, and Finding-driven Coverage. `plannedCoverageDimensions` expresses an investigation plan only and no longer proves formal coverage. The legacy coverage structure was removed from Host, test Harness, and example ledgers.
