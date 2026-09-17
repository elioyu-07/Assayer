# Vertical Slice 034: Agent, Model, and Lease Observability (C08.3)

> **Historical record.** This implementation slice predates the Platform
> Constitution v2 and is retained for traceability only; it is not current
> implementation guidance.


Agent requests carry bounded public decisionReason; adapters may expose model duration/retry. TurnTrace links tool duration and Host Operations while hiding provider stacks. Host writes decision/model events into the same Scan stream with agentTurnId/requestId/operationId closure. Router records lease and transport lifecycle; Host assigns contiguous sequence.

Manifest marks captured versus not-exposed telemetry; tokens, reconnects, and browser process metrics are not fabricated. Public rationale is explanatory, not Finding/Assessment evidence.
