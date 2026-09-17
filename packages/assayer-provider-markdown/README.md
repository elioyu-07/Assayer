# Markdown Provider restoration baseline

Date: 2026-09-17. Status: restored and component-tested; **not accepted for
end-to-end audit execution**.

This is an independently packaged, optional capability Provider. Its Python
implementation is under `src/assayer_document_navigation`; it depends on the
SDK and is not part of the platform wheel or the default runtime bundle.
Ordinary audit plugins remain declaration-only. No legacy plugin runtime or
browser implementation is restored by this change.

Design records:

- `design/changes/restore-markdown-audit-slice.json`
- `design/changes/markdown-provider-baseline-validation.json` (refines the
  first stage to restoration and baseline validation; end-to-end work remains)

## Reused assets and observed behavior

| Boundary | Evidence in this baseline |
| --- | --- |
| Parser | Existing source restored; headings, paragraphs, front matter, tables and fenced code retain line ranges and excerpts |
| Navigation | Existing bounded pagination, include filtering and digest checks pass |
| Provider contract | Descriptor inspection and capability negotiation pass |
| Catalog | The package's declared entry point loads the real Provider; no browser installation is required |
| Host collection | Real `BoundCapabilityProvider` returns registered, source-bound Evidence and replays an identical request |
| Source failure | A changed file without a digest override returns `source_changed`; a removed file returns `source_error`; neither produces Evidence |
| Packaging | Provider and SDK wheels built from a temporary source copy and installed into a temporary target; entry-point discovery and parsing succeeded outside the repository |
| Compiled execution | Contract compilation, Run creation, Provider binding, source discovery and structured Evidence collection succeed for one Markdown file |

The packaging probe used the current interpreter's third-party dependencies.
It establishes package contents and entry-point usability, not clean-environment
release conformance. Temporary wheels were discarded, not published.

Focused regression command from the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_markdown_navigation.py -v
```

Ten tests cover this baseline. The catalog unit test isolates metadata discovery
only, using real entry points read from the package declaration. The separate
wheel probe verifies actual installed metadata.

## Actual integration path

```text
declaration compiler -> compiled contract -> controller.start
    -> ProviderRegistry -> controller.bind_provider
    -> BoundCapabilityProvider.discover_work_items
    -> missing MarkdownNavigationProvider.discover_sources
```

The separate, tested collection path uses an explicitly constructed WorkItem:

```text
Markdown file -> MarkdownNavigationProvider.collect
    -> BoundCapabilityProvider validation -> issued Evidence
```

That collection test does not prove source discovery, durable snapshots,
ReviewBatch submission, recovery, or canonical-result publication.

## Known gaps to address before audit acceptance

Status note (2026-09-17): items 1, 3, 4 and 5 below are now covered by the
compiled-path tests in `tests/test_markdown_navigation.py` and
`tests/test_compiled_interactive.py`; item 2 is partly closed and still needs
the durable snapshot. The end-to-end acceptance target at the end of this file
remains the gate that decides audit acceptance, not these component results.

1. Repeated identical paragraphs under the same heading receive identical unit
   IDs. Existing duplicate tests only cover different headings. The element
   model also needs explicit hierarchy and parser-version guarantees.
2. The Provider rereads files rather than retaining a durable immutable
   snapshot. Its legacy `sourceDigest` override can no longer override the
   request digest as of `enforce-the-markdown-source-pin`: the Host-discovered
   WorkItem state digest is now the only pin and a scope digest may only repeat
   it. Retaining bytes from discovery (a durable immutable snapshot) is still
   open.
3. The compiled controller enumerates JSON nodes rather than consuming the
   Provider's Markdown unit model, and does not drain navigation pages into an
   exhaustive review universe. Parent objects can repeat whole-document data.
4. The compiled submission path must resolve Evidence against the issued
   source-bound records before accepting supported decisions.
5. Durable resume, canonical-result derivation and result replay must be proven
   through the compiled path; existing standalone components are not that proof.

## End-to-end acceptance target

The first execution milestone uses one Markdown file and one declared Check.
It preserves existing platform components and optional capabilities while
concentrating acceptance on this path.

- [ ] Provider discovery freezes the authorized source and its element model.
- [ ] Every element has an unambiguous identity, source range and bounded content.
- [ ] Review planning includes every required element, Check and Dimension,
  across all source pages, without accepting partial enumeration as complete.
- [ ] All five decision states are represented with their required explanation
  or valid source support; unknown and blocked never imply a valid pass.
- [ ] Missing, duplicate, foreign, fabricated and stale submissions are rejected
  without changing accepted coverage.
- [ ] Accepted batches survive a process restart; retry is idempotent.
- [ ] Source changes and read failures produce explicit failure/recovery facts.
- [ ] Canonical results derive from the durable Ledger and can be regenerated
  with stable identity and equivalent conclusions.
- [ ] One installed user-facing entry completes the real workflow, including
  interruption and continuation, without requiring internal protocol knowledge.

The next implementation change should define Provider-owned discovery and the
frozen Markdown element contract, then connect the existing compiled controller.
It needs a new checked Design Confirmation covering the actual integration
files. This baseline does not mark any of the unchecked acceptance items done.
