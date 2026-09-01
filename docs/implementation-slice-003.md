# Vertical Slice 003: Object Identity Validation and Promotion

`ObjectIdentityAdapter` returns structured identity results without executable selectors. `inspect_object(candidateId)` accepts only a Candidate or AuditObject in the current Scan. Exactly one complete match promotes to `eligible`; zero yields `not_found`; two or more yields `ambiguous` without selecting an object; changed semantics creates a new PageCandidate. ObjectVerification persists all results and supports idempotent retry; adapter contradictions fail closed.

The current adapter is deterministic test code and does not read real DOM or perform actions. Before safe actions are integrated, real-browser candidates must map to this same ObjectVerification contract.
