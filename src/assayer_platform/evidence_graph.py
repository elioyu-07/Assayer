"""Generic candidate-to-finding traceability for platform reviews.

The graph compresses Agent work without deleting source candidates.  Domain
plugins still decide what a candidate means; the platform validates identity,
membership, evidence closure, and coverage.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
import hashlib
import json

from .contract import DECISION_STATES, PlatformContractError


CANDIDATE_DISPOSITIONS = frozenset({"pending", "confirmed", "suppressed", "merged", "needs_review"})


@dataclass(frozen=True)
class EvidenceCandidate:
    candidate_id: str
    work_item_id: str
    check_id: str
    check_version: str
    fingerprint: str
    evidence_refs: tuple[str, ...] = ()
    disposition: str = "pending"

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (
            self.candidate_id, self.work_item_id, self.check_id,
            self.check_version, self.fingerprint,
        )):
            raise PlatformContractError("INVALID_CANDIDATE", "Candidate identity and fingerprint are required")
        if self.disposition not in CANDIDATE_DISPOSITIONS:
            raise PlatformContractError("INVALID_CANDIDATE", "Candidate disposition is not supported")
        refs = tuple(self.evidence_refs)
        if any(not isinstance(item, str) or not item for item in refs) or len(set(refs)) != len(refs):
            raise PlatformContractError("INVALID_CANDIDATE", "Candidate Evidence references must be unique nonempty strings")
        object.__setattr__(self, "evidence_refs", refs)


@dataclass(frozen=True)
class RootCauseGroup:
    group_id: str
    candidate_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()
    affected_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.group_id or not self.candidate_ids:
            raise PlatformContractError("INVALID_ROOT_CAUSE_GROUP", "Root-cause group identity and members are required")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise PlatformContractError("INVALID_ROOT_CAUSE_GROUP", "Root-cause group members must be unique")
        if any(not isinstance(item, str) or not item for item in (*self.evidence_refs, *self.affected_dimensions)):
            raise PlatformContractError("INVALID_ROOT_CAUSE_GROUP", "Group references must be nonempty strings")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise PlatformContractError("INVALID_ROOT_CAUSE_GROUP", "Group Evidence references must be unique")
        if len(set(self.affected_dimensions)) != len(self.affected_dimensions):
            raise PlatformContractError("INVALID_ROOT_CAUSE_GROUP", "Affected dimensions must be unique")


@dataclass(frozen=True)
class FindingRecord:
    finding_id: str
    group_id: str
    decision: str
    affected_dimensions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (self.finding_id, self.group_id, self.reason)):
            raise PlatformContractError("INVALID_FINDING_RECORD", "Finding identity, group, and reason are required")
        if self.decision not in DECISION_STATES:
            raise PlatformContractError("INVALID_FINDING_RECORD", "Finding decision is not supported")
        if not self.affected_dimensions or any(not item for item in self.affected_dimensions):
            raise PlatformContractError("INVALID_FINDING_RECORD", "Finding must affect at least one dimension")
        if not self.evidence_refs or len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise PlatformContractError("INVALID_FINDING_RECORD", "Finding must cite unique Evidence references")


@dataclass(frozen=True)
class EvidenceGraph:
    candidates: tuple[EvidenceCandidate, ...] = ()
    groups: tuple[RootCauseGroup, ...] = ()
    findings: tuple[FindingRecord, ...] = ()
    evidence_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        candidate_ids = [item.candidate_id for item in self.candidates]
        group_ids = [item.group_id for item in self.groups]
        finding_ids = [item.finding_id for item in self.findings]
        for values, code, label in (
            (candidate_ids, "INVALID_CANDIDATE", "Candidate"),
            (group_ids, "INVALID_ROOT_CAUSE_GROUP", "Root-cause group"),
            (finding_ids, "INVALID_FINDING_RECORD", "Finding"),
        ):
            if len(values) != len(set(values)):
                raise PlatformContractError(code, f"{label} identities must be unique")
        candidates = {item.candidate_id: item for item in self.candidates}
        groups = {item.group_id: item for item in self.groups}
        claimed: dict[str, str] = {}
        for group in self.groups:
            for candidate_id in group.candidate_ids:
                candidate = candidates.get(candidate_id)
                if candidate is None:
                    raise PlatformContractError("INVALID_ROOT_CAUSE_GROUP", "Group references an unknown candidate")
                prior = claimed.get(candidate_id)
                if prior is not None and prior != group.group_id:
                    raise PlatformContractError("INVALID_ROOT_CAUSE_GROUP", "A candidate belongs to multiple root-cause groups")
                claimed[candidate_id] = group.group_id
                candidate_refs = set(candidate.evidence_refs)
                if not set(group.evidence_refs).issubset(candidate_refs | set(self.evidence_ids)):
                    raise PlatformContractError("EVIDENCE_CLOSURE_FAILED", "Group Evidence is not traceable to its candidates")
        for finding in self.findings:
            group = groups.get(finding.group_id)
            if group is None:
                raise PlatformContractError("INVALID_FINDING_RECORD", "Finding references an unknown root-cause group")
            if not set(finding.affected_dimensions).issubset(set(group.affected_dimensions)):
                raise PlatformContractError("INVALID_FINDING_RECORD", "Finding dimension is outside its root-cause group")
            available = set(group.evidence_refs) | set(self.evidence_ids)
            if not set(finding.evidence_refs).issubset(available):
                raise PlatformContractError("EVIDENCE_CLOSURE_FAILED", "Finding Evidence is not traceable to its group")

    @property
    def covered_candidate_ids(self) -> frozenset[str]:
        return frozenset(item.candidate_id for item in self.candidates if item.disposition != "pending")

    @property
    def pending_candidate_ids(self) -> frozenset[str]:
        return frozenset(item.candidate_id for item in self.candidates if item.disposition == "pending")

    @property
    def coverage_complete(self) -> bool:
        return not self.pending_candidate_ids


def conservative_root_cause_groups(candidates: Iterable[EvidenceCandidate]) -> tuple[RootCauseGroup, ...]:
    """Group only exact same-object, same-check, same-fingerprint candidates."""
    buckets: dict[tuple[str, str, str, str], list[EvidenceCandidate]] = defaultdict(list)
    for candidate in candidates:
        buckets[(candidate.work_item_id, candidate.check_id, candidate.check_version, candidate.fingerprint)].append(candidate)
    groups: list[RootCauseGroup] = []
    for key, members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        group_id = "group:" + ":".join(key)
        groups.append(RootCauseGroup(
            group_id,
            tuple(item.candidate_id for item in members),
            tuple(dict.fromkeys(ref for item in members for ref in item.evidence_refs)),
        ))
    return tuple(groups)


def stable_candidate_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Return a conservative, source-identity fingerprint for a candidate.

    Plugins may provide an explicit ``fingerprint``/``candidateFingerprint``.
    Otherwise the adapter hashes only stable source facts.  It deliberately
    does not perform semantic or fuzzy matching: grouping must never hide two
    different source observations.
    """
    explicit = candidate.get("fingerprint", candidate.get("candidateFingerprint"))
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    stable = {
        "rule": candidate.get("rule_id", candidate.get("ruleId", "")),
        "object": candidate.get("object_id", candidate.get("objectId", "")),
        "line": candidate.get("line"),
        "chapter": candidate.get("chapter"),
        "message": candidate.get("message", ""),
        "evidence": candidate.get("evidence", ""),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def candidate_dispositions_from_decisions(
    decisions: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    """Project the Spec-compatible review statuses onto generic dispositions."""
    result: dict[str, str] = {}
    status_map = {
        "CONFIRMED": "confirmed", "SUPPRESSED": "suppressed",
        "MERGED": "merged", "UNVERIFIED": "needs_review",
    }
    for decision in decisions:
        disposition = status_map.get(str(decision.get("status", "")).upper())
        if not disposition:
            continue
        for candidate_id in decision.get("candidate_ids", ()) or ():
            if isinstance(candidate_id, str) and candidate_id:
                result[candidate_id] = disposition
    return result


def build_candidate_evidence_graph(
    candidates: Iterable[Mapping[str, Any]], *, work_item_id: str,
    check_id: str, check_version: str,
    decisions: Iterable[Mapping[str, Any]] = (),
) -> EvidenceGraph:
    """Adapt a plugin candidate collection to the platform evidence graph.

    This is intentionally a projection.  The original plugin payload and
    candidate IDs remain authoritative; the graph only adds generic coverage
    and conservative exact-match grouping metadata.
    """
    decisions = tuple(decisions)
    dispositions = candidate_dispositions_from_decisions(decisions)
    adapted: list[EvidenceCandidate] = []
    for raw in candidates:
        candidate_id = raw.get("candidate_id", raw.get("candidateId"))
        if not isinstance(candidate_id, str) or not candidate_id:
            raise PlatformContractError("INVALID_CANDIDATE", "Candidate ID is required")
        evidence = raw.get("evidence", ())
        refs = tuple(evidence) if isinstance(evidence, (tuple, list)) else ((evidence,) if isinstance(evidence, str) and evidence else ())
        adapted.append(EvidenceCandidate(
            candidate_id=candidate_id,
            work_item_id=work_item_id,
            check_id=str(raw.get("rule_id", raw.get("ruleId", check_id)) or check_id),
            check_version=check_version,
            fingerprint=stable_candidate_fingerprint(raw),
            evidence_refs=refs,
            disposition=dispositions.get(
                candidate_id,
                raw.get("disposition", "pending")
                if raw.get("disposition", "pending") in CANDIDATE_DISPOSITIONS
                else "pending",
            ),
        ))
    groups = conservative_root_cause_groups(adapted)
    dimensions_by_candidate: dict[str, set[str]] = defaultdict(set)
    for decision in decisions:
        dimension = decision.get("dimension")
        if not isinstance(dimension, str) or not dimension:
            continue
        for candidate_id in decision.get("candidate_ids", ()) or ():
            if isinstance(candidate_id, str):
                dimensions_by_candidate[candidate_id].add(dimension)
    if dimensions_by_candidate:
        groups = tuple(
            RootCauseGroup(
                group.group_id,
                group.candidate_ids,
                group.evidence_refs,
                tuple(sorted({dimension for candidate_id in group.candidate_ids
                              for dimension in dimensions_by_candidate.get(candidate_id, ())})),
            )
            for group in groups
        )
    return EvidenceGraph(tuple(adapted), groups)


def render_candidate_evidence_graph(graph: EvidenceGraph) -> dict[str, Any]:
    """Serialize the generic projection without replacing plugin payloads."""
    return {
        "candidateCount": len(graph.candidates),
        "coveredCandidateCount": len(graph.covered_candidate_ids),
        "pendingCandidateIds": sorted(graph.pending_candidate_ids),
        "coverageComplete": graph.coverage_complete,
        "rootCauseGroups": [
            {"groupId": group.group_id, "candidateIds": list(group.candidate_ids),
             "evidenceRefs": list(group.evidence_refs),
             "affectedDimensions": list(group.affected_dimensions)}
            for group in graph.groups
        ],
    }


def validate_candidate_evidence_graph_projection(value: Mapping[str, Any]) -> None:
    """Validate the portable, additive graph projection emitted by a plugin."""
    if not isinstance(value, Mapping):
        raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Candidate graph projection must be an object")
    required = ("candidateCount", "coveredCandidateCount", "coverageComplete", "pendingCandidateIds", "rootCauseGroups")
    if any(field not in value for field in required):
        raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Candidate graph projection is incomplete")
    counts = (value["candidateCount"], value["coveredCandidateCount"])
    if any(not isinstance(item, int) or item < 0 for item in counts):
        raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Candidate graph counts must be non-negative integers")
    pending = value["pendingCandidateIds"]
    groups = value["rootCauseGroups"]
    if not isinstance(pending, (tuple, list)) or any(not isinstance(item, str) or not item for item in pending):
        raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Pending candidate IDs must be nonempty strings")
    if not isinstance(groups, (tuple, list)):
        raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Root-cause groups must be an array")
    group_ids: set[str] = set()
    members: set[str] = set()
    for group in groups:
        if not isinstance(group, Mapping):
            raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Root-cause groups must be objects")
        group_id = group.get("groupId")
        candidate_ids = group.get("candidateIds")
        if not isinstance(group_id, str) or not group_id or group_id in group_ids:
            raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Root-cause group IDs must be unique nonempty strings")
        if not isinstance(candidate_ids, (tuple, list)) or not candidate_ids:
            raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Root-cause groups require members")
        if any(not isinstance(item, str) or not item for item in candidate_ids) or members.intersection(candidate_ids):
            raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Root-cause group members must be unique")
        group_ids.add(group_id)
        members.update(candidate_ids)
    if value["coveredCandidateCount"] > value["candidateCount"]:
        raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Covered candidates cannot exceed candidate count")
    if bool(value["coverageComplete"]) != (not pending):
        raise PlatformContractError("INVALID_EVIDENCE_GRAPH", "Coverage flag does not match pending candidates")


__all__ = [
    "CANDIDATE_DISPOSITIONS", "EvidenceCandidate", "EvidenceGraph",
    "FindingRecord", "RootCauseGroup", "conservative_root_cause_groups",
    "stable_candidate_fingerprint", "candidate_dispositions_from_decisions",
    "build_candidate_evidence_graph", "render_candidate_evidence_graph",
    "validate_candidate_evidence_graph_projection",
]
