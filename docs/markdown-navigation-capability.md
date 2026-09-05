# Markdown Navigation Capability

## Purpose

Provide one reusable, evidence-preserving navigation capability for Markdown
documents. The capability locates document structure and returns immutable,
line-addressable units for domain plugins. It does not decide whether content
is correct, complete, ambiguous, or compliant.

This slice is intentionally limited to Markdown so the Spec Quality journey
can be validated before additional document formats are considered.

## Boundary

The navigation capability owns:

- UTF-8 Markdown loading and source digest calculation;
- headings and heading paths;
- paragraphs, lists, block quotes, tables, fenced code blocks, and front matter;
- stable unit identities within a frozen source;
- exact one-based source line ranges and immutable source excerpts;
- deterministic unit ordering and bounded page retrieval.

The consuming plugin owns:

- domain objects such as FR, AC, CASE, fields, states, or dependencies;
- quality rules and semantic interpretation;
- finding severity, remediation, and readiness;
- deciding whether a signal is a defect.

The Host owns provider discovery, capability negotiation, source identity,
Evidence persistence, pagination, checkpointing, and coverage accounting.

Platform callers obtain the capability through
`assayer_platform.builtin_provider_registry()` (or
`installed_provider_registry()` for entry-point extensions). This keeps
provider selection at the platform boundary and gives each caller a fresh,
isolated registry.

## Provider contract (MVP)

The provider exposes one capability, `document_navigation`, through the normal
capability-provider contract. A request scope contains:

```json
{
  "path": "/absolute/path/spec.md",
  "format": "markdown",
  "page": {"cursor": null, "size": 100},
  "include": ["headings", "paragraphs", "lists", "tables", "code", "quotes", "front_matter"]
}
```

The successful fact payload contains:

```json
{
  "format": "markdown",
  "sourceDigest": "sha256:...",
  "document": {"path": "/absolute/path/spec.md", "lineCount": 42},
  "units": [
    {
      "unitId": "heading:2:requirements",
      "kind": "heading",
      "headingPath": ["Requirements"],
      "startLine": 2,
      "endLine": 2,
      "excerpt": "## Requirements"
    }
  ],
  "nextCursor": null,
  "coverage": {"discovered": 1, "returned": 1}
}
```

Unit IDs are deterministic for the frozen source and include a collision-safe
ordinal when equivalent blocks repeat. A provider must never claim semantic
meaning from a heading name or a missing block. A malformed or unsupported
Markdown construct remains a navigable `raw_block` or a classified provider
failure; it is not silently discarded.

## Evidence and pagination rules

- Every unit has an exact source digest, path, start line, end line, and excerpt.
- A page is bounded by the negotiated provider limit; callers resume with the
  returned cursor and must not replay a consumed page.
- A source change invalidates the provider result and requires a new request.
- The provider returns structure in source order.
- The provider may add deterministic metadata such as table headers and list
  depth, but never a domain finding or severity.

## Spec integration acceptance

The Spec plugin is the first consumer. Acceptance requires that the same fixed
Spec corpus can be reviewed with:

1. complete navigation coverage for all returned units;
2. no final finding based only on a missing literal keyword;
3. exact source locators for every confirmed finding;
4. explicit `PASS`, `FINDING`, `UNVERIFIED`, or `NOT_APPLICABLE` for every
   applicable review unit;
5. a measurable reduction in duplicate/noisy candidates versus the current
   scanner baseline, without hiding semantic findings.

No JSON/YAML/PDF/web provider, new Spec rule, or real CLI acceptance is part of
this slice.
