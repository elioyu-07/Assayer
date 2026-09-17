# CI Support Matrix v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-14 |
| Status | Active release policy |
| Owner | Release Engineering |

## 1. Supported Python range

All Assayer Python distributions declare `requires-python = ">=3.11"`. The CI
matrix therefore exercises every declared minor version currently in scope:

| Python | Fast gate | Full gate | Purpose |
|---|---:|---:|---|
| 3.11 | Required | Required | Minimum supported runtime |
| 3.12 | Required | Not repeated | Compatibility coverage for the declared range |
| 3.13 | Required | Required | Latest supported runtime and clean operator reference |

An unsupported Python minor MUST fail clearly at package/runtime readiness. A
green 3.11 job does not establish support for 3.13, and a local 3.13 trial does
not replace CI evidence.

## 2. Gate definitions

The `fast` job runs static, resilience, unit, and lightweight integration tests,
and it enforces the authored-text language preflight: a repository text file
that is neither English nor an allowlisted locale surface fails the profile
with exit code 2 before any test runs. The `full` job runs the same preflight.
Both fast and full jobs install the wheel-build toolchain (`setuptools`,
`wheel`, and `uv`) explicitly before verification; no runner-global build tool
is part of the support contract.
The `full` job adds the complete non-browser and browser/MCP regression suite,
including the installed-package checks appropriate to that job. The
`install-matrix` job builds and validates split-distribution installation at
Python 3.11 and 3.13.

The full gate is intentionally required at the minimum and latest supported
minor rather than repeated unchanged at every intermediate minor. A failure in
any matrix entry blocks the corresponding CI gate and release admission.

## 3. Platform scope

The automated matrix currently runs on `ubuntu-latest`. macOS arm64 with
CPython 3.13 is the clean Codex operator reference environment and is covered
by the operator release gate; it is not silently represented as a passing CI
job. Windows is not an officially supported platform in v1.

## 4. Consistency requirements

The following sources MUST agree before release:

- every package `requires-python` declaration;
- `.github/workflows/verification.yml` matrix entries;
- README and getting-started support statements;
- bundle/runtime compatibility diagnostics; and
- the environment facts in the operator evidence bundle.

Changing the support range requires a versioned update to this document and a
matching CI matrix change. Historical operator evidence for another release
candidate cannot be reused as current CI evidence.
