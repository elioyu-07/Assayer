# Vertical Slice 020: Release-Grade Integration and Fault Injection (B09)

Browser/Session failures converge to `BROWSER_SESSION_FAILED`; intentional Host rejection such as cross-origin blocking does not kill Session. Playwright launch, navigation, Context operations, and screenshots use explicit constrained timeouts. SDK exception details are not exposed. Scan fails on browser Session failure; registry close attempts all Sessions even when one cleanup fails.

Deterministic `scripts/resilience_scan.py` checks empty/swallowed exceptions, unbounded loops, and missing timeout on external browser calls, producing JSON only. Acceptance covers navigation timeout, Context crash, post-failure rejection, cleanup continuation, zero blocking findings, full tests, and real Chromium regression. Browser actions and side-effectful requests are never retried automatically; `result_unknown` uses Operation/recovery barriers.
