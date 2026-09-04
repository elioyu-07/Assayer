# Vertical Slice 067: Capability and Budget Negotiation

## Goal

Implement the provider-contract formula that a usable capability is the
intersection of platform policy, provider declaration, plugin requirement,
and user scope. No participant may widen the resulting profile.

## Negotiation input

`CapabilityNegotiator` accepts:

- one already conforming `ProviderRegistration`;
- the selected Check's required capability names;
- a platform `CapabilityProfile` containing allowed names and optional limit
  ceilings;
- an optional user `CapabilityProfile` containing granted names and optional
  limit ceilings; and
- business scope validated against the provider's registered scope schema.

When a provider requires user scope, an absent user profile grants nothing.
For a read-only provider that explicitly does not require user scope, absence
defaults only the requested capability names; it never includes unrelated
provider or platform capabilities.

## Deterministic result

The result identifies requested and granted capabilities, the selected
provider identity and version, effective limits, and one denial reason for
each missing requirement. Denials distinguish:

- `provider_absent`;
- `platform_denied`; and
- `user_scope_missing`.

Provider limits are mandatory. Platform and user limits may only reduce them;
the effective value is the minimum positive integer supplied for each limit.
Invalid scope, capability names, or limits fail before provider construction or
source access.

A `ready` result is the only result allowed to create `PlatformContext`.
That context now preserves both negotiated capability names and effective
limits. A blocked result cannot be converted into an execution context.

`ProviderRegistry.select_for_capabilities` selects one provider that covers
the complete required set. Zero matches fail missing and multiple matches fail
ambiguous; the registry never silently assembles unrelated providers.

## Public contract

`capability-negotiation.schema.json` closes the output shape and requires a
non-empty denial list for blocked results and no denials for ready results.
The runtime also enforces set inclusion and minimum-limit semantics that JSON
Schema cannot express by itself.

## Acceptance evidence

Focused tests cover four-way intersection, non-widening, deterministic denial
ownership, mandatory user scope, optional read-only scope, limit clamping,
invalid scope and budgets, blocked-context rejection, complete-set provider
selection, ambiguity, and schema-valid output.

## Remaining integration boundary

This slice supplies the shared negotiation primitive. Existing built-in
plugins still use their compatibility registration capability set when their
current product adapters construct `PlatformContext`. The next slice must add
a provider-bound runner path and make external provider-backed Runs consume
only negotiated contexts. It must then bind provider requests, failures, and
Evidence to the selected provider identity, version, capability, algorithms,
scope, and limits.
