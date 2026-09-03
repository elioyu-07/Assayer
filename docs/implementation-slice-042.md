# Vertical Slice 042: Generic Interactive Inspection Paging (M3 foundation)

The domain-neutral interactive controller now pages WorkItem inspection using
the selected plugin's declared `inspectBatching` and `maxBatchSize` constraints.
Callers may provide an opaque cursor and receive a bounded range plus the next
cursor. The transport validates page size and cursor ownership before the
plugin is invoked.

Every returned InvestigationPacket still passes the existing WorkItem,
Evidence, dimension, and recovery gates and is checkpointed independently. A
plugin that forbids inspection batching is automatically limited to one
WorkItem per call. This is a platform capability; no Spec or frontend-specific
branch was added.

This slice pages WorkItems, not fields inside a domain packet. M3 still needs
layered Evidence packets and on-demand expansion for a single WorkItem whose
source contains many low-level candidates.
