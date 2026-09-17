# Frontend Simple SDK Migration Evidence

| Metadata | Value |
|---|---|
| Date | 2026-09-15 |
| Status | Superseded by Platform Constitution v2; retained as historical migration reference |
| Scope | Earlier domain migration and split distribution |

> **Superseded.** This document predates the Platform Constitution v2 and is
> retained as a historical migration reference. The Platform Constitution v2
> and the v1 contracts govern current platform work; this content is not
> current implementation guidance.


> **Historical evidence.** Concrete domain names and old distribution mechanics
> below are retained only to explain a past migration. They are not platform
> requirements and must not be copied into a new ordinary plugin.

## Build boundary

`scripts/build_distributions.py` compiles `plugins/frontend-audit/` with the
Simple SDK compiler before building `assayer-plugin-frontend-audit`. There is
no hand-written `src/assayer_frontend_audit` package and no direct-build
descriptor under `packages/assayer-plugin-frontend-audit`; the compiler output
is the only Frontend runtime and release entry point.

The generated wheel was inspected to confirm that it contains the compiler
registration, common-review contract, browser capability declaration, and
`frontend_object` subject kind. It contains no hand-written DomainResult
implementation.

The aggregate builder also runs generated-package conformance before invoking
the wheel backend. A clean temporary environment installed that wheel together
with the SDK, Host, and browser provider; one common-review Run completed with
`completed / scanned_no_issue`.

## Runtime gate

The integration test
`tests.test_browser_playwright.PlaywrightReadonlyIntegrationTest.test_real_chromium_provider_run_reaches_semantic_boundary_and_resumes`
uses a local `/orders` page and a real Chromium snapshot with the
compiler-generated registration. It verifies:

- the Run reaches `awaiting_agent_decision`;
- the Run resumes from that boundary; and
- the investigation persists `browser_snapshot` Evidence.

Command:

```bash
PYTHONPATH=src:packages/assayer-provider-browser/src \
  .venv/bin/python -m unittest \
  tests.test_browser_playwright.PlaywrightReadonlyIntegrationTest.test_real_chromium_provider_run_reaches_semantic_boundary_and_resumes
```

Result on 2026-09-15 after the hard cut: 1 test passed in 2.438 seconds.

The non-browser fast gate passed 750 tests, and the complete gate passed 822
tests in 48.733 seconds after the old package, direct build descriptor, and
adapter-only tests were removed. The architecture boundary checker and the
bundle's compiler-owned Frontend source gate also passed.

This closes the code-level migration and hard-cut gate. J04/J05/J08 operator
acceptance and natural-language routing remain separate release gates; they do
not authorize restoring the removed adapter.

## Natural-language developer gate

The Assayer Codex bundle was rebuilt and reinstalled from the local Marketplace
on 2026-09-15 as `0.1.2+codex.20260915014354`. A fresh private runtime exposed
17 MCP tools, including `verify_plugin_source`. Calling that tool for this
Policy Pack passed all four stages: compilation, generated contracts, isolated
wheel build, and installed lifecycle (including resume, pagination, and replay).
The private runtime now installs both the Markdown and browser capability
providers; the bundle builder asserts this provider set before publication.
