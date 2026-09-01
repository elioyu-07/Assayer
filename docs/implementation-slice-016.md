# Vertical Slice 016: Real-Browser Recovery Barrier

Adds `BrowserRecoveryAdapter` sharing Session/Context with page, object, action, and network adapters. `begin_case` saves normalized route/layer/object fingerprint and probe state without raw DOM or values. Host records fixed inverses for expand/collapse, `noop` for focus/scroll, and `refresh_only` otherwise. Targeted inverse recovery then checks `url_route`, `page_layer`, `active_tab`, `overlay_state`, `control_state`, `object_identity`, `pending_requests`, `write_request`, and `local_visual`. Uncertain/failed attempts refresh and replay same-origin entrypoints; only all-match returns `restored`.

Playwright request events prove pending convergence; without tracking, recovery cannot pass optimistically. Real tests cover targeted success, refresh replay, unknown pending, writes, ambiguity, crashes, and restart.
