# Ordinary Plugin Development Standard v1

| Metadata | Value |
|---|---|
| Document version | 1.0.0 |
| Date | 2026-09-16 |
| Status | Active; declaration-only ordinary-plugin standard |

This standard refines [Platform Constitution v2](platform-constitution-v2.md)
for declaration-only ordinary plugins. It does not create an alternate runtime
or an exception for complex domains.

## Required source

```text
plugin.yaml
checks.yaml
semantic-review.md
cases/
```

No Python, executable code, custom schema, source adapter, Provider binding,
Agent prompt protocol, transport, lifecycle hook, persistence, or external
effect is permitted in an ordinary plugin.

`plugin.yaml` declares identity, business version, and a supported input kind.
`checks.yaml` declares stable domain Checks and Dimensions, applicability,
outcomes, severity, remediation, and examples. `semantic-review.md` explains
domain judgment in plain language. `cases/` proves positive, negative,
unknown, and not-applicable meaning.

## Compilation

The platform compiler validates declarations and derives one typed
`compiled-plugin.json`. Derived fields include the normalized manifest,
capability requirements, review plan, Agent guidance, validation rules,
business-case acceptance, contract digest, and release identity. Authors never
edit generated fields or maintain parallel metadata.

## Verification gate

The Host-owned gate must prove, in order:

1. allowed source structure and declaration validity;
2. semantic guidance and business-case completeness;
3. supported input and Provider capability availability;
4. complete element enumeration and one decision per review atom;
5. digest, identity, replay, recovery, and canonical-result integrity; and
6. exact compiled-artifact installation readiness.

The only ordinary-plugin release artifact is `compiled-plugin.json`. Installation
and publication consume that exact artifact and its digest. Provider releases
are governed by the separate Provider contract.

## Change control

Every change begins with an approved Design Confirmation. A semantic, safety,
public-interface, persistence, or release change requires human approval and
full re-verification. Retired ordinary-plugin runtime paths are hard deleted;
no compatibility alias may be added to make an old artifact run.
