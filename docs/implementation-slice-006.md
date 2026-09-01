# Vertical Slice 006: Evidence Collection and Raw Visual

`EvidenceAdapter` accepts only current-Scan, current-PageState, uniquely verified objects. Evidence binds Scan, PageState, Object, optional Case, and capture revision. Host sanitizes, normalizes, and digests payloads; adapters cannot claim `sanitized=true`. Sensitive keys, Bearer tokens, and query secrets are masked before SQLite. Explicit Raw Visual capture records image digest, dimensions, bounding box, annotation, and relative path. Unconfirmed sanitization, missing location, or ambiguity records failed Screenshot facts only. Files use stable IDs, exclusive creation, and `0600`; Evidence/Screenshot are immutable and idempotent.

This slice creates structured Evidence and `kind=raw_visual` Screenshots. Formal `kind=issue` screenshots are derived later by `prepare_decision(issue_found)`.
