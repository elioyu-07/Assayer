# Assayer Alpha User Journey and End-to-End Definition of Done

| Metadata | Value |
|---|---|
| Document version | 1.1.0 |
| Date | 2026-09-01 |
| Status | J00-J03 confirmed; J04 in progress |
| Owner | Product Owner / Agent Runtime / Host Core |

This document is the sole user-journey baseline for the Assayer productization phase. It defines whether a user can actually use the product and when completion may be claimed. Plugin, Skill, MCP, Host, tests, and observability are implementation means and cannot independently prove product completion.

## 1. Alpha User and Scope

### 1.1 Target user

The Alpha user is a frontend, test, or product engineer who can access a test or pre-release Web site. The user does not need to understand MCP, protocol envelopes, schemas, revisions, leases, ledger formats, or repository directories.

### 1.2 Alpha primary journey

Alpha first locks the anonymous URL audit: the user provides an HTTP(S) URL that requires no login in Codex CLI (primary client; Desktop is the compatibility client). Assayer uses real Chromium to observe pages, inspect objects, perform controlled interactions, collect evidence, apply rules, and deliver results.

Password login, SSO/MFA, source attribution proof, and automatic screenshot sanitization are outside this iteration. Interruption-safe checkpoint recovery is now the separately gated J06b requirement. Automatic screenshot sanitization is governed separately as a later security enhancement and does not block the current journey build; real trial runs must use controlled synthetic or sanitized test data and must not claim production screenshot safety.

### 1.3 User-visible promises

- After one installation, the user only needs to provide a URL in Codex Desktop or CLI using natural language;
- The user is not asked to edit MCP configuration or enter internal paths or protocol fields;
- During a scan the user can see what is happening, whether it is still running, and why it stopped;
- At the end the system clearly states the result status, actual coverage, uncovered scope, and next step;
- After a failure the system provides actionable diagnostics and allows a retry without reconfiguration;
- Every formal issue can be traced to a concrete page object, rule, evidence, and screenshot.

## 2. End-to-End Primary Journey

| Stage | User action | System behavior | Observable acceptance condition |
|---|---|---|---|
| J00 Scope and gates | Review the Alpha promise | Fix target user, primary journey, non-goals, and Definition of Done | The team has one productization plan; component status cannot override journey status |
| J01 Acquire and install | Install one Assayer deliverable | One installation includes Skill, MCP, rules, schemas, and runtime resources; Python/Chromium are external prerequisites | Installation instructions list only external prerequisites; no repository paths or manual internal dependency setup |
| J02 Activate and discover | Open a new CLI session (primary acceptance; Desktop compatibility acceptance) | Load the Skill automatically and resolve the local Assayer MCP on demand; the Agent can discover the tool list | A new CLI session discovers and can call `mcp__assayer__*`; deferred MCP is resolved automatically; stale sessions explicitly request refresh instead of silently missing tools |
| J03 Start an audit | Say “audit this URL: <URL>” | Agent creates a Scan; Host starts real Chromium and binds the URL | User does not enter `scanId`, `runId`, request envelopes, or output paths |
| J04 Runtime experience | Wait or ask for progress | Agent/Host explore pages, select objects, execute safe Cases, collect evidence, and report status continuously | Startup, observation, inspection, recovery, and convergence are distinguishable; long model reasoning is not mistaken for lease expiry; disconnects/process exits fail clearly |
| J05 Result experience | Review results | Produce `completed`, `partial`, or `failed` terminal state with plain-language summary, scope, issues, and next step | All three terminal states are understandable; `needs_review` names the concrete gap rather than showing only the label |
| J06 Recovery and reuse | Stop and continue a Run, retry as instructed, or provide another URL | J06a creates an independent second Scan without state leakage; J06b resumes an interrupted Run from the last durable boundary | A second run starts directly; `Esc -> continue` neither duplicates nor loses accepted work, and stale requests cannot overwrite newer progress |
| J07 Real acceptance | Repeat the primary journey in a clean directory outside the repository | Desktop and CLI use the same deliverable and Host core continuously | With fresh configuration and only a URL, at least one success and one failure-recovery scenario complete |
| J08 Release and feedback | Upgrade, uninstall, or file an issue | Version is identifiable; upgrade is rollback/retry safe; uninstall leaves no broken configuration; diagnostic artifacts can be attached | User knows version and cleanup result; issue reports include reproducible run IDs and diagnostic references without secrets |

## 3. Terminal-State Contract

### completed

The declared scope's pages, entrypoints, objects, and enabled rules meet their coverage requirements; formal issues passed evidence, screenshot, and recovery gates. The result must state both what was inspected and what was not.

### partial

Some scope is complete and its valid decisions are deliverable; uncovered scope, blockers, and recommended action must be listed. `partial` is not a synonym for success and must not hide a failed stage.

### failed

Login (if later enabled), browser startup, network, credentials, ledger, environment integrity, Agent Runtime, or MCP process failure makes formal conclusions unavailable. Deliver diagnostics and recovery guidance only, not issue conclusions.

### needs_review

This is an object-by-rule result, not a scan terminal state. In plain language it must identify the missing discriminating fact, safe actions attempted, and condition the user can provide next; model confidence or `pageListCount` alone is never a reason.

## 4. Alpha Definition of Done

Alpha end-to-end user-journey completion may be claimed only when all conditions below hold:

1. Create a completely new Codex CLI configuration in an empty directory outside the repository;
2. Install one Assayer deliverable; internal Skill/MCP/rules/schemas require no manual copying or path edits;
3. Python and Chromium are declared external prerequisites and missing dependencies fail clearly at install/startup;
4. A new CLI session automatically discovers the same Skill and MCP; a new Desktop task passes compatibility acceptance through the same path;
5. The user enters only natural language and a URL, never internal protocol fields;
6. Real Chromium runs one `scanned_no_issue` positive case, one controlled negative case producing a formal `issue_found`, and one diagnosable partial/failed case;
7. Runtime shows phase events, progress, and stop reason; long reasoning does not cause a false lease timeout; disconnects fail quickly;
8. Non-developers can understand and act on `completed`, `partial`, and `failed` results;
9. A second run needs no reconfiguration and never reuses conclusions from an invalid Scan;
10. Artifacts include ledger, result summary, diagnostics, runtime events, and integrity status, with no credentials, cookies, Authorization, raw page bodies, or hidden reasoning;
11. Full tests execute Chromium/MCP for real with zero skips; passing tests are a gate, not a substitute for items 1-10;
12. Upgrade, uninstall, and feedback paths have reproducible records, and internal absolute paths do not leak to users or release configuration.
13. Interruption recovery passes J06b: after an Agent interruption or Host restart, the Host reports the authoritative durable boundary and exact next action; replayed or stale operations cannot duplicate or overwrite accepted audit records.

## 5. Real Acceptance Matrix

| ID | Scenario | Client | Required observation |
|---|---|---|---|
| UAT-01 | Install and first discovery with fresh configuration outside repository | Desktop, CLI | Neither client requires manual internal dependency setup to discover Skill/MCP |
| UAT-02 | Anonymous URL positive case | Desktop, CLI | Real Chromium completes and produces `completed`; at least one object closes a `scanned_no_issue` loop |
| UAT-03 | Anonymous URL negative case (controlled data) | At least once each in Desktop and CLI | Real Chromium reaches issue decision, result display, and artifact closure; screenshot sanitization status is honest and unsanitized images are not treated as production evidence |
| UAT-04 | Bounded partial | Any client | Completed scope, uncovered scope, blocker, and next step are explicit |
| UAT-05 | Startup or runtime failure | Any client | Clear `failed`, no formal conclusions; diagnostics identify ownership and permit retry |
| UAT-06 | Second use | Desktop, CLI | Without reinstalling or changing configuration, create an independent Scan for another URL |
| UAT-07 | Continuity | Desktop, CLI | Three consecutive independent Scans per client with no silent skip, downgrade, hang, or stale-state reuse |
| UAT-08 | Upgrade and uninstall | Installation environment | New sessions load the upgraded version; uninstall leaves no stale broken MCP configuration |
| UAT-09 | Interruption and resume | CLI | Interrupt before and after a checkpoint acknowledgement, then continue; the Host resumes from the unique durable boundary with no duplicate decision or Evidence loss |

B07c (automatic sensitive-region identification and pixel sanitization) is deferred to a later security phase and is not a J01-J08 completion gate. Current trials must use controlled synthetic or already sanitized data; any `sanitizationStatus=not_performed` screenshot is explicitly marked in results and must not be copied into a production report or described as screenshot-safe. Harness issue samples do not replace real user-journey acceptance.

## 6. Evidence That Does Not Constitute Completion

- Only a green deterministic Harness or unit-test suite;
- Only README or slice documentation claiming completion;
- Operation only from repository directories and developer absolute paths;
- Only Desktop or only CLI working;
- MCP tools existing while users still construct protocol JSON;
- Verifying only `scanned_no_issue` without issue, partial, and failed experiences;
- Runtime events that cannot be associated with the result shown to the user;
- Silent skip or smoke downgrade when dependencies are absent.

## 7. Relationship to Other Documents

- [Product contract](product-contract.md) owns product goals, rule scope, and user-visible safety commitments; this document owns the Alpha delivery boundary, user journey, and sole completion gate;
- [LLM Agent investigation plan](llm-agent-integration-plan.md) records implementation slices and must map them to J01-J08; it cannot independently claim product completion;
- [Observability governance](observability-governance.md) defines the fact and diagnostic closure required by J04-J08;
- Legacy C/B slices are historical implementation records. When they conflict, this document's user journey and Definition of Done take precedence.

## 8. Current CLI Acceptance Record

On 2026-09-01, a real Codex CLI fresh session received only `http://localhost:8081/#/lease-mock`, automatically loaded the Assayer Plugin Skill and deferred local MCP, called `start_audit` to launch real Chromium, and completed FUA-10 decisions for three objects. This is real CLI evidence for J02 activation/discovery and J03 audit start.

The same run lasted about 10 minutes 44 seconds without an intermediate progress message, and its public `decisionReason` count was 0/38, so J04 has not passed. Performance and experience baselines, fixes, and revalidation gates are defined in [vertical slice 037](implementation-slice-037.md).
