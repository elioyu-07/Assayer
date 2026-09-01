# Vertical Slice 027: Codex Skill and Bounded Agent Loop (C04)

C04 adds the project Skill, model-independent AgentLoop, DecisionAgent, and ToolInvoker. Skill routes frozen rules and Host references without copying rule prose. Each turn carries allowlisted structured tools, request and turn IDs, idempotency, current revision, public rationale, and untrusted-data boundaries.

Unknown tools, selectors, scripts, duplicate decisions, model failures, and unknown-result replay fail closed with bounded stop reasons. A scripted Agent completed the Host loop and produced Findings/scanned_no_issue without Host business conclusions. Dynamic Router and leases belong to C05.
