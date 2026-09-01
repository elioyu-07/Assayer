# Vertical Slice 018: Real Object-Level Screenshots (B07b)

With `includeRawVisual=true`, `BrowserEvidenceAdapter` rebinds the object and captures a Host-probed Playwright PNG crop; Agent supplies no selector, script, or coordinates. Host validates PageState, origin, fingerprint, and bounding box. Missing, ambiguous, changed, out-of-bounds, or unsupported pages fail closed without an image. Raw Visual stores PNG metadata, dimensions, source/problem bounds, and SHA-256; files are immutable `0600` and conflicts refuse overwrite. `prepare_decision(issue_found)` derives an independent `kind=issue` screenshot.

B07b explicitly records `sanitizationStatus=not_performed` and `sanitized=false`; it cannot enter formal `issue_found`. B07c adds sensitive-region masking later. Tests cover crop, digest, permissions, binding, and sanitization gate.
