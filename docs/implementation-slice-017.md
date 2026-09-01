# Vertical Slice 017: Real Structured Evidence (B07a)

Adds `BrowserEvidenceAdapter` reusing page, identity, Session, and network guard. Fixed Host probes collect minimum DOM/ARIA facts: role, accessible-name presence, control type/state classes, route, page layer, and network summary. Fields retain only classes such as `empty/non_empty`; text is reduced to length/identity facts. Object is rebound and fingerprint/PageState/origin checked before capture. Sanitization covers Bearer tokens, parameter secrets, email, phone, and long numeric identifiers.

`includeRawVisual=true` belongs to B07b; this slice persists structured `runtime_dom` Evidence only and produces no image. Tests cover fake and real Chromium paths.
