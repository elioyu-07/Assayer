# Vertical Slice 066: Capability Provider Conformance

## Goal

Turn the frozen capability-provider contract into a shared registration gate
before browser, file, repository, API, database, or other providers can enter
the platform catalog.

## Descriptor and registration boundary

`load_provider_descriptor` validates the existing
`capability-provider.schema.json`, platform API compatibility, and a valid
object-shaped business scope schema. It converts the document into immutable,
domain-neutral provider and capability identities.

`ProviderRegistration` binds that descriptor to one provider factory.
`ProviderRegistry` rejects non-conforming or duplicate provider identities and
requires explicit selection when several providers offer the same capability.
Installed distributions use the independent `assayer.providers` Python entry
point group; provider discovery does not share the plugin entry point group.

## Semantic conformance

The schema is necessary but insufficient for cross-field laws. The shared
provider conformance gate additionally verifies:

- unique capability names;
- explicit user scope for controlled-action capabilities;
- one retry policy per failure code;
- the complete v1 provider failure taxonomy;
- mandatory `resolve_unknown_first` behavior for `result_unknown`;
- at least one versioned Evidence identity or normalization algorithm;
- a callable provider factory; and
- when constructed for release validation, descriptor identity and a callable
  bounded `collect` operation.

Provider construction tests do not call `collect` or connect to a live source.
Initialization exceptions are replaced by fixed actionable errors rather than
exposed through conformance output.

## Release interface

`assayer-provider-check MODULE:ATTRIBUTE` loads one registration, registration
factory, or provider registry, constructs implementations without live source
access, and emits a schema-valid `provider-conformance.schema.json` report.
Every failure carries a stable code, `CPV1-*` invariant, explanation, and next
action.

## Acceptance evidence

Focused tests cover valid construction, independent descriptor files, platform
API incompatibility, invalid scope schemas, duplicate capabilities and failure
codes, missing failure semantics, unsafe unknown-result retries,
controlled-action authorization, algorithm identity, missing or malformed
runtime implementations, registry conflicts and ambiguity, and sanitized CLI
failure output.

## Remaining work

This slice gates provider metadata and implementation shape. The next provider
slice must compute the effective capability profile as the intersection of
platform policy, provider declaration, plugin requirement, and user scope,
then bind provider failures and Evidence to that negotiated profile. Provider
package-resource and isolated installation gates remain separate follow-up
work.
