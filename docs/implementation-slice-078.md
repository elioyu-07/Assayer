# Implementation Slice 078 — Result-Feature Conformance

Status: implemented

Plugin registrations may declare the additive `evidence_graph` result feature.
Package/runtime conformance then verifies that a constructed implementation
explicitly marks the feature as enabled. This prevents a package from claiming
portable evidence coverage without implementing it.

The declaration is opt-in and defaults to no feature, so existing third-party
plugins remain compatible. Built-in Spec Quality and Config Quality declare and
implement the feature. Frontend remains unchanged until its own result path
adopts the projection.

The gate checks capability declaration only; it does not execute a browser,
invoke an Agent, or infer domain semantics.
