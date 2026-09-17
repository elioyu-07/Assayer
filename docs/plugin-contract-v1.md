# Assayer Plugin Contract v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-16 |
| Status | Active; ordinary plugins use the compiled contract only |
| Authority | [Platform Constitution v2](platform-constitution-v2.md) |
| Artifact | `compiled-plugin.json` |

## 1. Purpose

The Plugin Contract is the single boundary between domain requirements and the
Assayer platform. An ordinary plugin supplies meaning; the platform supplies
all execution, source, safety, evidence, coverage, persistence, and release
capabilities.

## 2. Authoring surface

The only ordinary-plugin source files are:

```text
plugin.yaml
checks.yaml
semantic-review.md
cases/
```

The source is declaration-only and zero Python. It cannot define a parser,
Provider, Agent, transport, lifecycle hook, result protocol, platform schema,
or external effect.

## 3. Compiled contract

The platform compiler normalizes declarations into one typed, versioned
`compiled-plugin.json`. The artifact includes the derived identity, supported
input kind, Checks and Dimensions, applicability and terminal-state meaning,
Provider requirements, semantic guidance, business cases, contract digest, and
release identity. The source tree is not an installable runtime.

The digest is bound to the business version and the confirmation record. A
semantic change requires a new version and new confirmation; an existing
artifact is never silently rewritten.

## 4. Review and result boundary

The Host enumerates the complete source universe and plans every
element × Check × Dimension atom. The Agent returns one decision for each atom:
`satisfied`, `violated`, `not_applicable`, `unknown`, or `blocked`.

The Host resolves Support against the frozen Provider snapshot, validates
membership and invariants, persists the Coverage Ledger, and derives the
canonical Result. Plugins cannot add a result protocol or bypass unresolved
coverage.

## 5. Installation and release

Verification must pass source, declaration, semantic, business-case,
enumeration, exhaustive-coverage, digest, and artifact-identity gates. Success
produces exactly `compiled-plugin.json`; only that exact artifact may be
installed or published. A failed verification invalidates an earlier artifact
for the same source revision.

Provider distributions are a separate platform-owned concern. Provider
registration may use the Provider contract; ordinary plugin registration and
runtime entry points do not exist.
