# Layer Migration Validation — Spec and Config

Status: implemented

Spec now consumes the platform Markdown navigation adapter for its complete
navigation document, and its candidate checkpoints pass through the generic
review coverage validator before the existing Spec semantic validator. Config
continues to publish the platform evidence graph and uses the unified delivery
observer through `PlatformRunner`.

The migration is additive: existing payload fields, candidate IDs, checkpoint
records, and terminal result identities remain unchanged. Domain semantics
remain in the plugins. This slice validates the first two plugin migration
proofs before any legacy helper removal.
