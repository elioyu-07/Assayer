from assayer_plugin_sdk.simple import Candidate, Document, invariant, policy_plugin


@policy_plugin(
    id="test.simple-author",
    version="1.0.0",
    input="markdown",
    checks="checks.yaml",
    instructions="semantic-review.md",
)
class SimpleAuthor:
    @invariant(
        "decision-reason-required",
        guidance="Every decision requires a reason.",
        location="/decisions",
        positive=({"decisions": [{"value": {"reason": "explained"}}]},),
        negative=({"decisions": [{"value": {"reason": ""}}]},),
    )
    def decision_reason_required(self, value):
        return all(
            bool(item.get("value", {}).get("reason"))
            for item in value.get("decisions", ())
        )

    def scan(self, document: Document):
        if not document.contains("overview"):
            yield Candidate(
                "SIMPLE-001",
                "document overview",
                "The document lacks an overview.",
                document.absence("overview", scope=document.full_scope),
                "P2",
                "Add a concise overview.",
            )
