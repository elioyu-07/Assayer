# Layer Convergence L3 — Generic Evidence Collection Paging

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


Status: implemented

The platform now owns `EvidenceCollectionPager`, a domain-neutral immutable
collection pager. It validates item-ID alignment, preserves stable cursors,
supports declared mechanical groups, and enforces bounded page sizes.

Interactive collection expansion delegates to this pager. Plugin-declared
collection metadata and item payloads remain unchanged, while pagination and
cursor safety no longer need to be reimplemented by a domain plugin.

The pager does not decide whether an item is a finding and does not infer
semantic coverage; checkpoint persistence remains the review protocol's
responsibility.
