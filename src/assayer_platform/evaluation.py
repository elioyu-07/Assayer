"""Domain-neutral fixed evaluation corpus loading and validation."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, RefResolver

from .registry import _schema_root


def _validator() -> Draft202012Validator:
    root = _schema_root()
    schema = json.loads((root / "evaluation-corpus.schema.json").read_text(encoding="utf-8"))
    common = json.loads((root / "common.schema.json").read_text(encoding="utf-8"))
    resolver = RefResolver(schema["$id"], schema, store={
        schema["$id"]: schema,
        common["$id"]: common,
        "common.schema.json": common,
    })
    return Draft202012Validator(schema, resolver=resolver)


def load_evaluation_corpus(path: Path) -> dict[str, Any]:
    """Load a plugin-owned corpus and validate its generic envelope."""
    corpus = json.loads(path.read_text(encoding="utf-8"))
    validate_evaluation_corpus(corpus)
    return corpus


def validate_evaluation_corpus(corpus: Mapping[str, Any]) -> None:
    """Validate schema plus reference closure not expressible in JSON Schema."""
    _validator().validate(dict(corpus))
    case_ids: set[str] = set()
    document_ids: set[str] = set()
    for case in corpus["cases"]:
        case_id = case["caseId"]
        if case_id in case_ids:
            raise ValueError(f"Duplicate evaluation caseId: {case_id}")
        case_ids.add(case_id)
        documents = case["documents"]
        current_document_ids = [document["documentId"] for document in documents]
        if document_ids.intersection(current_document_ids) or len(current_document_ids) != len(set(current_document_ids)):
            raise ValueError(f"Duplicate documentId in evaluation corpus: {case_id}")
        document_ids.update(current_document_ids)
        if case["category"] == "cross_document":
            anchors = [document for document in documents if document["role"] == "anchor"]
            if len(anchors) != 1 or case.get("anchorDocumentId") != anchors[0]["documentId"]:
                raise ValueError(f"Cross-document case requires one matching anchor: {case_id}")
            if len(documents) < 2:
                raise ValueError(f"Cross-document case requires at least two documents: {case_id}")
            for document in documents:
                if "contentDigest" not in document:
                    raise ValueError(f"Cross-document case requires document digests: {case_id}")
                if "content" in document and hashlib.sha256(document["content"].encode("utf-8")).hexdigest() != document["contentDigest"]:
                    raise ValueError(f"Cross-document content digest is invalid: {case_id}")
            relationships = case.get("relationships", ())
            relationship_ids = [relationship["relationshipId"] for relationship in relationships]
            if not relationships or len(relationship_ids) != len(set(relationship_ids)):
                raise ValueError(f"Cross-document case requires unique relationships: {case_id}")
            for relationship in relationships:
                if relationship["fromDocumentId"] not in set(current_document_ids) or relationship["toDocumentId"] not in set(current_document_ids):
                    raise ValueError(f"Relationship references an unknown document: {case_id}")
        elif case.get("relationships"):
            raise ValueError(f"Relationships are only valid for cross-document cases: {case_id}")
        if case["category"] == "interrupted" and "recovery" not in case:
            raise ValueError(f"Interrupted case requires recovery metadata: {case_id}")
        expected = case["expected"]
        current_candidate_ids = [candidate["candidateId"] for candidate in expected["candidates"]]
        if len(current_candidate_ids) != len(set(current_candidate_ids)):
            raise ValueError(f"Duplicate candidateId in evaluation corpus: {case_id}")
        known = set(current_candidate_ids)
        for candidate in expected["candidates"]:
            if "documentIds" in candidate and not set(candidate["documentIds"]).issubset(set(current_document_ids)):
                raise ValueError(f"Candidate references an unknown document: {case_id}")
        for disposition in expected["suppressions"]:
            if disposition["candidateId"] not in known:
                raise ValueError(f"Suppression references unknown candidate: {case_id}")
        for disposition in expected["unverified"]:
            if disposition["candidateId"] not in known:
                raise ValueError(f"Unverified disposition references unknown candidate: {case_id}")
        current_finding_ids = [finding["findingId"] for finding in expected["findings"]]
        if len(current_finding_ids) != len(set(current_finding_ids)):
            raise ValueError(f"Duplicate findingId in evaluation corpus: {case_id}")
        handled: list[str] = [item["candidateId"] for item in expected["suppressions"]]
        handled.extend(item["candidateId"] for item in expected["unverified"])
        for finding in expected["findings"]:
            if not set(finding["candidateIds"]).issubset(known):
                raise ValueError(f"Finding references unknown candidate: {case_id}")
            handled.extend(finding["candidateIds"])
            if "documentIds" in finding and not set(finding["documentIds"]).issubset(set(current_document_ids)):
                raise ValueError(f"Finding references an unknown document: {case_id}")
            if case["category"] != "cross_document" and finding.get("severity") is None:
                raise ValueError(f"Confirmed finding requires severity: {case_id}")
        if case["category"] == "cross_document":
            relationship_ids = [relationship["relationshipId"] for relationship in case["relationships"]]
            relationship_reviews = expected.get("relationshipReviews")
            if not isinstance(relationship_reviews, list) or not relationship_reviews:
                raise ValueError(f"Cross-document case requires expected relationship reviews: {case_id}")
            reviewed_relationship_ids = [item["relationshipId"] for item in relationship_reviews]
            if len(reviewed_relationship_ids) != len(set(reviewed_relationship_ids)) or set(
                reviewed_relationship_ids
            ) != set(relationship_ids):
                raise ValueError(f"Cross-document relationship reviews must cover scope exactly: {case_id}")
            assigned_finding_ids: list[str] = []
            known_finding_ids = set(current_finding_ids)
            finding_by_id = {finding["findingId"]: finding for finding in expected["findings"]}
            for review in relationship_reviews:
                finding_ids = review["findingIds"]
                if not set(finding_ids).issubset(known_finding_ids):
                    raise ValueError(f"Relationship review references an unknown finding: {case_id}")
                if review["outcome"] == "COMPATIBLE" and finding_ids:
                    raise ValueError(f"Compatible relationship review cannot contain findings: {case_id}")
                if review["outcome"] != "COMPATIBLE" and not finding_ids:
                    raise ValueError(f"Non-compatible relationship review requires findings: {case_id}")
                if review["outcome"] == "UNVERIFIED" and any(
                    finding_by_id[finding_id].get("semanticType") != "unverified_dependency"
                    or finding_by_id[finding_id].get("severity") is not None
                    for finding_id in finding_ids
                ):
                    raise ValueError(f"Unverified relationship review requires dependency findings: {case_id}")
                if review["outcome"] == "FINDING" and any(
                    finding_by_id[finding_id].get("semanticType") == "unverified_dependency"
                    or finding_by_id[finding_id].get("severity") is None
                    for finding_id in finding_ids
                ):
                    raise ValueError(f"Finding relationship review cannot contain unverified dependencies: {case_id}")
                assigned_finding_ids.extend(finding_ids)
            if len(assigned_finding_ids) != len(set(assigned_finding_ids)) or set(
                assigned_finding_ids
            ) != known_finding_ids:
                raise ValueError(f"Cross-document findings must belong to one relationship review: {case_id}")
        elif expected.get("relationshipReviews"):
            raise ValueError(f"Relationship reviews are only valid for cross-document cases: {case_id}")
        if len(handled) != len(set(handled)):
            raise ValueError(f"Candidate has multiple terminal dispositions: {case_id}")
        if set(handled) != known:
            raise ValueError(f"Candidate lacks a terminal disposition: {case_id}")
        for merge in expected["merges"]:
            if not set(merge["candidateIds"]).issubset(known):
                raise ValueError(f"Merge references unknown candidate: {case_id}")
            if merge["findingId"] not in set(current_finding_ids):
                raise ValueError(f"Merge references unknown finding: {case_id}")


__all__ = ["load_evaluation_corpus", "validate_evaluation_corpus"]
