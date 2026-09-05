# Layer Convergence L1 — Platform Interface Skeleton

Status: implemented

The platform now exposes four domain-neutral protocol seams:

- `NavigationProvider` for stable source/navigation facts;
- `EvidenceCollectionProvider` for immutable evidence and bounded pages;
- `ReviewProtocol` for checkpoint and decision boundaries;
- `DeliveryObserver` for portable publication and diagnostics.

These are interfaces only. Existing runtime paths remain unchanged, and no
plugin or browser dependency was moved into the platform kernel. The
architecture boundary checker and protocol tests prove the interfaces remain
platform-only.
