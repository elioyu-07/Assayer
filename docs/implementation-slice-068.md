# Vertical Slice 068: Provider-Bound Execution and Evidence

## Goal

Turn a ready capability negotiation into one controlled provider execution
boundary. A provider may return source facts or a classified operational
failure, but it cannot create a compliance decision or place unchecked data in
the platform ledger.

## Host-created request

`BoundCapabilityProvider` is constructed only from one conforming provider
registration and one `ready` `CapabilityNegotiation`. Construction verifies
that provider identity and version still match before creating the provider
implementation.

For each WorkItem and required capability, the Host creates a request bound to:

- Run, WorkItem, Check, provider, and capability identity;
- the WorkItem source identity and state digest;
- the validated provider business scope;
- the exact negotiated timeout, byte, item, and concurrency limits; and
- a content-derived idempotency key.

An identical request returns the already known collection result and does not
call the provider again. This includes `result_unknown`: the known unknown is
returned with `resolve_unknown_first`, never replayed blindly.

## Fact and failure validation

Providers return `ProviderResponse`, containing either one or more facts or
one classified failure. The Host rejects responses with a mismatched request,
provider, version, or capability. Facts must use an Evidence kind declared by
that capability and must match the WorkItem source identity and state digest.
The negotiated item and byte ceilings are checked before Evidence is accepted.

Failures must use the provider descriptor's declared taxonomy. Provider error
details do not cross the boundary; the platform emits a stable safe message and
the registered retry policy. Uncaught provider exceptions become a generic
platform failure without copying exception text into a result.

The timeout is an operation budget, not a Run lifetime. Assayer still has no
fixed overall audit timeout. A provider operation that returns after its
ceiling is classified as timed out and its facts are discarded.

## Immutable Evidence binding

Every accepted Provider fact becomes `EvidenceRecord` bound to:

- the current Run and Host-created request;
- provider ID and version;
- the negotiated capability;
- source identity and state digest; and
- the provider's complete frozen algorithm-version map.

Before semantic decision, `PlatformKernel` requires a matching
`ProviderEvidenceExpectation`. It rejects incomplete bindings, unrequested
capabilities, undeclared Evidence kinds, stale source state, substituted
provider versions, and altered algorithm versions. Every capability required
by the Check must be represented by Provider Evidence.

Legacy built-in plugins remain an explicit provider-unbound compatibility
path. Provider-bound Evidence is rejected on that path instead of being
silently trusted.

## Runner integration

`PlatformRunner.run_with_provider` performs the order-sensitive sequence:

1. select the plugin and unique Check;
2. select exactly one provider covering every required capability;
3. negotiate platform policy, provider declaration, plugin requirement, user
   scope, and effective limits;
4. construct the bound provider only when negotiation is ready;
5. give the plugin only the bound provider and negotiated `PlatformContext`;
6. enforce Provider Evidence in the Kernel before decision and commit; and
7. close the provider at the execution boundary.

A blocked negotiation constructs neither provider nor plugin, so no source
access can occur before authorization and policy agree.

## Acceptance evidence

Deterministic tests cover request identity, limit propagation, idempotent
replay, complete ledger traceability, response identity substitution, source
and state substitution, undeclared Evidence kinds, byte-budget overflow,
unclassified failures, safe `result_unknown`, blocked pre-construction, and
Kernel rejection of altered provider version, state, or algorithms.

## Remaining boundary

This slice establishes the shared runtime primitive and a provider-bound batch
runner. Existing product adapters are not claimed as migrated. Provider package
validation, isolated installation, cross-process provider request recovery,
and measured multi-provider scheduling remain later M3 work.
