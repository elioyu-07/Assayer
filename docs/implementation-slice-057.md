# Vertical Slice 057: Generic Run Progress and Human Diary

## Goal

Make a long interactive plugin Run understandable while it is active and after
it stops, without requiring either the user or Agent to parse the canonical
ledger or infer whether the Host is hung.

## Platform behavior

Every generic interactive response includes a compact `progress` view with:

- current phase and lifecycle state;
- whether the platform, Agent, recovery, or nobody is being awaited;
- completed WorkItem, inspection, decision, and checkpoint counts;
- remaining inspection, decision, review, and failure counts;
- the durable required next step and a plain-language next action;
- the time at which the current lifecycle phase/state was entered;
- an explicit terminal flag.

The `awaiting_agent_decision` state states that durable Evidence is saved and
semantic Agent judgment is required. This is expected waiting, not a timeout.
Host-driven product calls may translate the low-level checkpoint or submission
step into `advance_plugin_run`, while diagnostic calls preserve their exact
next operation.

## Human diary

`platform-run.log` is no longer a sequence of field dumps. It now records:

- Run, plugin, and Check identity;
- readable state, phase, and progress counts;
- chronological lifecycle and Operation explanations;
- committed Decisions and recorded failures;
- current position, remaining work, waiting reason, and next action.

`platform-ledger.json` remains canonical and `platform-events.jsonl` remains
the machine timeline. The diary and progress block are derived views only; they
cannot alter Evidence, Decisions, coverage, recovery, or terminal validity.
Sensitive key/value patterns are redacted from diary text.

## Recovery and replay

Workflow events now record state and phase transitions with the previous state,
previous phase, required next step, and new phase. Terminal progress is rebuilt
from the terminal ledger and result metrics after a Host restart, so a replayed
terminal acknowledgement is identical to the original result view.

## Deterministic evidence

- Active lifecycle tests distinguish platform work from Agent semantic waiting
  and validate remaining work and next-action guidance.
- A generated diary is checked for identity, readable status, progress,
  expected waiting, and absence of the former `key=value` event format.
- Terminal responses expose terminal progress and survive exact replay through
  a replacement transport.
- A failed inspection Run exposes failure progress and a readable failure diary.
- The progress object validates against `plugin-progress.schema.json`.

## Acceptance boundary

Focused generic lifecycle tests and the complete fast non-browser suite are the
implementation gate. They do not constitute J04 user acceptance. The owner-run
clean Codex CLI journeys remain required before J04 is marked accepted, and no
real CLI or browser audit was started by this slice.
