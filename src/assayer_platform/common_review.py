"""Host-owned value types for the common incremental review language."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any

from .contract import PlatformContractError


APPLICABILITY_STATES = frozenset({"applicable", "not_applicable", "unknown"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low", "unknown"})
DIMENSION_VERDICTS = frozenset({
    "satisfied", "violated", "unresolved", "blocked", "conflicted", "not_applicable",
})
CANDIDATE_DISPOSITIONS = frozenset({"confirmed", "suppressed", "merged", "needs_review"})
RELATIONSHIP_VERDICTS = frozenset({"confirmed", "rejected", "unknown", "not_applicable"})
FINDING_SEVERITIES = frozenset({"P0", "P1", "P2", "P3", "P4"})
COMMON_REVIEW_CONTRACT = "common-review:1.1.0"
_ITEM_REF = re.compile(r"^I[1-9][0-9]*$")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformContractError(
            "INVALID_COMMON_REVIEW", f"{label} must be a nonempty string",
        )
    return value.strip()


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _unique_texts(values: object, label: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise PlatformContractError(
            "INVALID_COMMON_REVIEW", f"{label} must be an array of strings",
        )
    try:
        normalized = tuple(_required_text(item, label) for item in values)  # type: ignore[arg-type]
    except TypeError as error:
        raise PlatformContractError(
            "INVALID_COMMON_REVIEW", f"{label} must be an array of strings",
        ) from error
    if len(normalized) != len(set(normalized)):
        raise PlatformContractError(
            "INVALID_COMMON_REVIEW", f"{label} must not contain duplicates",
        )
    return normalized


@dataclass(frozen=True)
class Applicability:
    """Whether a declared rule or dimension applies to the reviewed subject."""

    state: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in APPLICABILITY_STATES:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Applicability state is unsupported",
            )
        reason = _optional_text(self.reason, "Applicability reason")
        if self.state != "applicable" and reason is None:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW",
                "Non-applicable or unknown review requires an applicability reason",
            )
        object.__setattr__(self, "reason", reason)

    def as_dict(self) -> dict[str, str]:
        return {
            "state": self.state,
            **({"reason": self.reason} if self.reason is not None else {}),
        }


@dataclass(frozen=True)
class Confidence:
    """Calibrated confidence in one semantic verdict."""

    level: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.level not in CONFIDENCE_LEVELS:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Confidence level is unsupported",
            )
        reason = _optional_text(self.reason, "Confidence reason")
        if self.level == "unknown" and reason is None:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Unknown confidence requires a reason",
            )
        object.__setattr__(self, "reason", reason)

    def as_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            **({"reason": self.reason} if self.reason is not None else {}),
        }


@dataclass(frozen=True)
class EvidenceSupport:
    """Task-visible Evidence handles supporting one common verdict."""

    refs: tuple[str, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "refs", _unique_texts(self.refs, "Evidence support refs"))
        object.__setattr__(self, "reason", _optional_text(self.reason, "Evidence support reason"))

    def as_dict(self) -> dict[str, object]:
        return {
            "refs": list(self.refs),
            **({"reason": self.reason} if self.reason is not None else {}),
        }


@dataclass(frozen=True)
class Unknown:
    """A bounded review outcome that cannot be concluded from available Evidence."""

    reason: str
    missing_information: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _required_text(self.reason, "Unknown reason"))
        object.__setattr__(
            self,
            "missing_information",
            _unique_texts(self.missing_information, "Missing information"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "missingInformation": list(self.missing_information),
        }


@dataclass(frozen=True)
class Escalation:
    """Explicit human or external follow-up required to resolve an atom."""

    reason: str
    required_action: str
    missing_information: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _required_text(self.reason, "Escalation reason"))
        object.__setattr__(
            self, "required_action", _required_text(self.required_action, "Required action"),
        )
        object.__setattr__(
            self,
            "missing_information",
            _unique_texts(self.missing_information, "Missing information"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "requiredAction": self.required_action,
            "missingInformation": list(self.missing_information),
        }


@dataclass(frozen=True)
class Finding:
    """Domain finding content before the Host assigns platform identity."""

    title: str
    message: str
    severity: str
    recommendation: str
    support: EvidenceSupport
    affected_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required_text(self.title, "Finding title"))
        object.__setattr__(self, "message", _required_text(self.message, "Finding message"))
        object.__setattr__(
            self, "recommendation", _required_text(self.recommendation, "Finding recommendation"),
        )
        if self.severity not in FINDING_SEVERITIES:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Finding severity is unsupported",
            )
        if not isinstance(self.support, EvidenceSupport) or not self.support.refs:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Finding requires at least one Evidence support ref",
            )
        object.__setattr__(
            self,
            "affected_dimensions",
            _unique_texts(self.affected_dimensions, "Finding affected dimensions"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "message": self.message,
            "severity": self.severity,
            "recommendation": self.recommendation,
            "support": self.support.as_dict(),
            **({
                "affectedDimensions": list(self.affected_dimensions),
            } if self.affected_dimensions else {}),
        }


@dataclass(frozen=True)
class DimensionVerdict:
    """One dimension conclusion in the common review language."""

    dimension: str
    verdict: str
    reason: str
    applicability: Applicability = field(default_factory=lambda: Applicability("applicable"))
    confidence: Confidence = field(default_factory=lambda: Confidence("medium"))
    support: EvidenceSupport = field(default_factory=EvidenceSupport)
    unknown: Unknown | None = None
    findings: tuple[Finding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimension", _required_text(self.dimension, "Dimension name"))
        object.__setattr__(self, "reason", _required_text(self.reason, "Dimension verdict reason"))
        if self.verdict not in DIMENSION_VERDICTS:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Dimension verdict is unsupported",
            )
        if not isinstance(self.applicability, Applicability):
            raise PlatformContractError("INVALID_COMMON_REVIEW", "Dimension applicability is invalid")
        if not isinstance(self.confidence, Confidence):
            raise PlatformContractError("INVALID_COMMON_REVIEW", "Dimension confidence is invalid")
        if not isinstance(self.support, EvidenceSupport):
            raise PlatformContractError("INVALID_COMMON_REVIEW", "Dimension support is invalid")
        if self.verdict == "not_applicable" and self.applicability.state != "not_applicable":
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Not-applicable verdict requires matching applicability",
            )
        if self.verdict != "not_applicable" and self.applicability.state == "not_applicable":
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Not-applicable subjects cannot carry another verdict",
            )
        if self.verdict == "unresolved" and not isinstance(self.unknown, Unknown):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Unresolved dimension requires an Unknown explanation",
            )
        if self.verdict != "unresolved" and self.unknown is not None:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Only unresolved dimensions may carry Unknown",
            )
        try:
            findings = tuple(self.findings)
        except TypeError as error:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Dimension findings must be an array",
            ) from error
        if any(not isinstance(item, Finding) for item in findings):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Dimension findings must use the common Finding type",
            )
        if findings and self.verdict not in {"violated", "conflicted"}:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW",
                "Only violated or conflicted dimensions may carry confirmed findings",
            )
        if any(
            item.affected_dimensions
            and self.dimension not in item.affected_dimensions
            for item in findings
        ):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW",
                "A dimension finding must include its reviewed dimension",
            )
        object.__setattr__(self, "findings", findings)
        if self.verdict in {"satisfied", "violated", "conflicted"} and not self.support.refs:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Conclusive dimension verdict requires Evidence support",
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "verdict": self.verdict,
            "reason": self.reason,
            "applicability": self.applicability.as_dict(),
            "confidence": self.confidence.as_dict(),
            "support": self.support.as_dict(),
            **({"unknown": self.unknown.as_dict()} if self.unknown is not None else {}),
            **({
                "findings": [item.as_dict() for item in self.findings],
            } if self.findings else {}),
        }


@dataclass(frozen=True)
class CandidateDisposition:
    """Disposition of one Host-planned deterministic candidate."""

    disposition: str
    reason: str
    support: EvidenceSupport = field(default_factory=EvidenceSupport)
    finding: Finding | None = None
    merged_into: str | None = None
    unknown: Unknown | None = None
    escalation: Escalation | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _required_text(self.reason, "Candidate disposition reason"))
        if self.disposition not in CANDIDATE_DISPOSITIONS:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Candidate disposition is unsupported",
            )
        if not isinstance(self.support, EvidenceSupport):
            raise PlatformContractError("INVALID_COMMON_REVIEW", "Candidate support is invalid")
        merged_into = _optional_text(self.merged_into, "Merge target")
        object.__setattr__(self, "merged_into", merged_into)
        if (self.disposition == "confirmed") != isinstance(self.finding, Finding):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Only a confirmed candidate requires a Finding",
            )
        if (self.disposition == "merged") != (merged_into is not None):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Merged disposition requires exactly one merge target",
            )
        unresolved = int(self.unknown is not None) + int(self.escalation is not None)
        if self.disposition == "needs_review" and unresolved != 1:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Needs-review disposition requires Unknown or Escalation",
            )
        if self.disposition != "needs_review" and unresolved:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Resolved candidate cannot carry Unknown or Escalation",
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "disposition": self.disposition,
            "reason": self.reason,
            "support": self.support.as_dict(),
            **({"finding": self.finding.as_dict()} if self.finding is not None else {}),
            **({"mergedInto": self.merged_into} if self.merged_into is not None else {}),
            **({"unknown": self.unknown.as_dict()} if self.unknown is not None else {}),
            **({"escalation": self.escalation.as_dict()} if self.escalation is not None else {}),
        }


@dataclass(frozen=True)
class RelationshipVerdict:
    """Conclusion about one Host-planned relationship atom."""

    relationship: str
    verdict: str
    reason: str
    applicability: Applicability = field(default_factory=lambda: Applicability("applicable"))
    confidence: Confidence = field(default_factory=lambda: Confidence("medium"))
    support: EvidenceSupport = field(default_factory=EvidenceSupport)
    unknown: Unknown | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "relationship", _required_text(self.relationship, "Relationship name"))
        object.__setattr__(self, "reason", _required_text(self.reason, "Relationship verdict reason"))
        if self.verdict not in RELATIONSHIP_VERDICTS:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Relationship verdict is unsupported",
            )
        if not isinstance(self.applicability, Applicability):
            raise PlatformContractError("INVALID_COMMON_REVIEW", "Relationship applicability is invalid")
        if not isinstance(self.confidence, Confidence):
            raise PlatformContractError("INVALID_COMMON_REVIEW", "Relationship confidence is invalid")
        if not isinstance(self.support, EvidenceSupport):
            raise PlatformContractError("INVALID_COMMON_REVIEW", "Relationship support is invalid")
        if (self.verdict == "not_applicable") != (self.applicability.state == "not_applicable"):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Relationship applicability and verdict must agree",
            )
        if (self.verdict == "unknown") != isinstance(self.unknown, Unknown):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Unknown relationship verdict requires Unknown only",
            )
        if self.verdict in {"confirmed", "rejected"} and not self.support.refs:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Conclusive relationship verdict requires Evidence support",
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "relationship": self.relationship,
            "verdict": self.verdict,
            "reason": self.reason,
            "applicability": self.applicability.as_dict(),
            "confidence": self.confidence.as_dict(),
            "support": self.support.as_dict(),
            **({"unknown": self.unknown.as_dict()} if self.unknown is not None else {}),
        }


ReviewValue = DimensionVerdict | CandidateDisposition | RelationshipVerdict | Unknown | Escalation


def _object(
    value: object, label: str, *, required: set[str], optional: set[str] | None = None,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlatformContractError("INVALID_COMMON_REVIEW", f"{label} must be an object")
    allowed = required | (optional or set())
    if not required.issubset(value) or set(value) - allowed:
        raise PlatformContractError("INVALID_COMMON_REVIEW", f"{label} has an invalid shape")
    return value


def _parse_applicability(value: object) -> Applicability:
    item = _object(value, "Applicability", required={"state"}, optional={"reason"})
    return Applicability(item["state"], item.get("reason"))


def _parse_confidence(value: object) -> Confidence:
    item = _object(value, "Confidence", required={"level"}, optional={"reason"})
    return Confidence(item["level"], item.get("reason"))


def _parse_support(value: object) -> EvidenceSupport:
    item = _object(value, "Evidence support", required={"refs"}, optional={"reason"})
    return EvidenceSupport(item["refs"], item.get("reason"))


def _parse_unknown(value: object) -> Unknown:
    item = _object(
        value, "Unknown", required={"reason", "missingInformation"},
    )
    return Unknown(item["reason"], item["missingInformation"])


def _parse_escalation(value: object) -> Escalation:
    item = _object(
        value, "Escalation",
        required={"reason", "requiredAction", "missingInformation"},
    )
    return Escalation(item["reason"], item["requiredAction"], item["missingInformation"])


def _parse_finding(value: object) -> Finding:
    item = _object(
        value, "Finding",
        required={"title", "message", "severity", "recommendation", "support"},
        optional={"affectedDimensions"},
    )
    return Finding(
        item["title"], item["message"], item["severity"], item["recommendation"],
        _parse_support(item["support"]),
        item.get("affectedDimensions", ()),
    )


def _parse_review_value(kind: str, value: object) -> ReviewValue:
    if kind == "dimension":
        item = _object(
            value, "Dimension verdict",
            required={
                "dimension", "verdict", "reason", "applicability", "confidence", "support",
            },
            optional={"unknown", "findings"},
        )
        raw_findings = item.get("findings", ())
        if not isinstance(raw_findings, Sequence) or isinstance(raw_findings, (str, bytes)):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Dimension findings must be an array",
            )
        return DimensionVerdict(
            item["dimension"], item["verdict"], item["reason"],
            _parse_applicability(item["applicability"]),
            _parse_confidence(item["confidence"]),
            _parse_support(item["support"]),
            _parse_unknown(item["unknown"]) if "unknown" in item else None,
            tuple(_parse_finding(finding) for finding in raw_findings),
        )
    if kind == "candidate":
        item = _object(
            value, "Candidate disposition",
            required={"disposition", "reason", "support"},
            optional={"finding", "mergedInto", "unknown", "escalation"},
        )
        return CandidateDisposition(
            item["disposition"], item["reason"], _parse_support(item["support"]),
            _parse_finding(item["finding"]) if "finding" in item else None,
            item.get("mergedInto"),
            _parse_unknown(item["unknown"]) if "unknown" in item else None,
            _parse_escalation(item["escalation"]) if "escalation" in item else None,
        )
    if kind == "relationship":
        item = _object(
            value, "Relationship verdict",
            required={
                "relationship", "verdict", "reason", "applicability", "confidence", "support",
            },
            optional={"unknown"},
        )
        return RelationshipVerdict(
            item["relationship"], item["verdict"], item["reason"],
            _parse_applicability(item["applicability"]),
            _parse_confidence(item["confidence"]),
            _parse_support(item["support"]),
            _parse_unknown(item["unknown"]) if "unknown" in item else None,
        )
    if kind == "unknown":
        return _parse_unknown(value)
    if kind == "escalation":
        return _parse_escalation(value)
    raise PlatformContractError("INVALID_COMMON_REVIEW", "Common review decision kind is unsupported")


def _value_support_refs(value: ReviewValue) -> tuple[str, ...]:
    if isinstance(value, DimensionVerdict):
        refs = list(value.support.refs)
        for finding in value.findings:
            refs.extend(finding.support.refs)
        return tuple(dict.fromkeys(refs))
    if isinstance(value, RelationshipVerdict):
        return value.support.refs
    if isinstance(value, CandidateDisposition):
        refs = list(value.support.refs)
        if value.finding is not None:
            refs.extend(value.finding.support.refs)
        return tuple(dict.fromkeys(refs))
    return ()


@dataclass(frozen=True)
class CommonReviewDecision:
    """One Agent verdict bound only to a task-local review item handle."""

    item_ref: str
    value: ReviewValue

    def __post_init__(self) -> None:
        if not isinstance(self.item_ref, str) or _ITEM_REF.fullmatch(self.item_ref) is None:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Review decision requires a task-local I<n> item ref",
            )
        if not isinstance(self.value, (
            DimensionVerdict, CandidateDisposition, RelationshipVerdict, Unknown, Escalation,
        )):
            raise PlatformContractError("INVALID_COMMON_REVIEW", "Review decision value is invalid")

    @property
    def kind(self) -> str:
        return {
            DimensionVerdict: "dimension",
            CandidateDisposition: "candidate",
            RelationshipVerdict: "relationship",
            Unknown: "unknown",
            Escalation: "escalation",
        }[type(self.value)]

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return _value_support_refs(self.value)

    def as_dict(self) -> dict[str, object]:
        return {
            "itemRef": self.item_ref,
            "kind": self.kind,
            "value": self.value.as_dict(),
        }


@dataclass(frozen=True)
class CommonReviewSubmission:
    """Complete verdict set for one current Host-issued ReviewBatch."""

    decisions: tuple[CommonReviewDecision, ...]

    def __post_init__(self) -> None:
        try:
            decisions = tuple(self.decisions)
        except TypeError as error:
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Common review decisions must be an array",
            ) from error
        if not decisions or any(not isinstance(item, CommonReviewDecision) for item in decisions):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Common review requires typed decisions",
            )
        refs = tuple(item.item_ref for item in decisions)
        if len(refs) != len(set(refs)):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Each task item may be decided only once",
            )
        object.__setattr__(self, "decisions", decisions)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommonReviewSubmission":
        root = _object(value, "Common review submission", required={"decisions"})
        raw = root["decisions"]
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW", "Common review decisions must be an array",
            )
        decisions: list[CommonReviewDecision] = []
        for raw_decision in raw:
            item = _object(
                raw_decision, "Common review decision",
                required={"itemRef", "kind", "value"},
            )
            kind = item["kind"]
            if not isinstance(kind, str):
                raise PlatformContractError(
                    "INVALID_COMMON_REVIEW", "Common review decision kind must be a string",
                )
            decisions.append(CommonReviewDecision(
                item["itemRef"], _parse_review_value(kind, item["value"]),
            ))
        return cls(tuple(decisions))

    def validate_task_membership(
        self, *, expected_item_refs: Sequence[str], allowed_evidence_refs: Sequence[str],
    ) -> None:
        expected = tuple(expected_item_refs)
        if len(expected) != len(set(expected)) or any(
            not isinstance(item, str) or _ITEM_REF.fullmatch(item) is None for item in expected
        ):
            raise PlatformContractError(
                "INVALID_COMMON_REVIEW_TASK", "Host review item refs are malformed",
            )
        submitted = tuple(item.item_ref for item in self.decisions)
        unknown_items = set(submitted) - set(expected)
        if unknown_items:
            raise PlatformContractError(
                "UNKNOWN_REVIEW_ITEM", "Common review references an item outside the current batch",
            )
        if set(submitted) != set(expected):
            raise PlatformContractError(
                "INCOMPLETE_REVIEW_SUBMISSION",
                "Common review must decide every current batch item exactly once",
            )
        allowed = set(allowed_evidence_refs)
        cited = tuple(ref for decision in self.decisions for ref in decision.evidence_refs)
        if set(cited) - allowed:
            raise PlatformContractError(
                "UNKNOWN_REVIEW_EVIDENCE",
                "Common review cites Evidence outside the current task",
            )

    def as_dict(self) -> dict[str, object]:
        return {"decisions": [item.as_dict() for item in self.decisions]}


__all__ = [
    "APPLICABILITY_STATES",
    "CONFIDENCE_LEVELS",
    "CANDIDATE_DISPOSITIONS",
    "COMMON_REVIEW_CONTRACT",
    "DIMENSION_VERDICTS",
    "FINDING_SEVERITIES",
    "RELATIONSHIP_VERDICTS",
    "Applicability",
    "CandidateDisposition",
    "CommonReviewDecision",
    "CommonReviewSubmission",
    "Confidence",
    "DimensionVerdict",
    "Escalation",
    "EvidenceSupport",
    "Finding",
    "RelationshipVerdict",
    "Unknown",
]
