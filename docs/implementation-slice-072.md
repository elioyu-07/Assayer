# Vertical Slice 072: Shared Result and Recovery Conformance

## Goal

Make final result validity a platform-enforced invariant shared by batch,
interactive, built-in, and external plugin paths. A matching fixture outcome
is insufficient when its proof, recovery, commit, or publication chain is
invalid.

## Result conformance gate

`inspect_result_conformance` validates one terminal `PlatformRunResult`
against its canonical ledger. The schema-backed report checks:

- Run identity, status, failures, Decisions, and receipts match the ledger;
- the ledger ends in a matching terminal event;
- every Decision traces to one WorkItem and InvestigationPacket;
- Findings close the investigated dimensions and satisfy the result gate;
- Evidence IDs, references, WorkItem identity, Check identity, and source
  identity close;
- the latest recovery outcome permits every non-failed formal Decision;
- every Decision has one matching authoritative receipt;
- completed coverage has no failed or undecided inspected WorkItem;
- failed formal results suppress Decisions, receipts, and artifacts while the
  ledger may retain invalidated diagnostic history; and
- every published artifact is bound to receipts from the same Run.

Violations identify a stable `RCV1-*` invariant and an actionable next step.

## Enforcement points

- `PlatformKernel.publish` refuses batch publication before calling an
  external publisher when result conformance fails.
- Interactive terminal persistence runs the same gate before writing the
  terminal ledger.
- Isolated external-plugin fixture validation runs the same gate before
  comparing the plugin's declared expected outcome.

The gate is domain-neutral and does not inspect plugin-specific business
meaning.

## Active recovery barrier

Interactive recovery is no longer passive telemetry. The latest explicit
recovery event wins. `uncertain` and `failed` block Decision commit until a
later `restored` or `not_required` event exists. After commit, recovery for
that WorkItem cannot change. Recovery Operations use normalized platform
statuses while the domain-neutral recovery outcome remains in the event.

Failed Runs are allowed to retain invalidated Decision history in the ledger;
the formal failed result suppresses it. This permits honest diagnosis without
presenting unsafe conclusions.

## Acceptance evidence

Deterministic tests prove valid result acceptance, schema-backed reports,
receipt mismatch rejection before publication, latest-recovery invalidation,
active interactive recovery blocking and repair, failed-result suppression,
and existing isolated release fixture compatibility.

## Remaining boundary

This slice validates the current platform result and ledger. Emitting the
portable `canonical-result.schema.json` representation from every adapter is
the next M3 result task. Real CLI/browser acceptance and measured workload
claims remain owner-run activities.
