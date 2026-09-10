"""Standalone frontend-audit plugin (independent from the platform kernel).

This package is the externalized Frontend audit domain.  It depends on the
Assayer platform (``assayer``) and exposes one ``assayer.plugins`` entry point,
``registration``, that the platform loads after static package validation.

The module bodies in ``runtime.py`` and ``legacy_runtime.py`` are relocated
from the platform source and use absolute ``assayer_platform`` imports; this
package is the single source of truth for the Frontend domain.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from assayer_platform import DomainResultContract, PluginRegistration

from .runtime import FrontendAuditPlugin, FrontendDecisionProvider
from .legacy_runtime import (
    FrontendDecisionCommitter,
    FrontendLedgerCommitter,
    ProductFrontendRuntime,
)


FRONTEND_SCOPE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["url"],
    "properties": {"url": {"type": "string", "format": "uri"}},
}

_SEMANTIC_REVIEW = Path(__file__).with_name("semantic-review.md")

FRONTEND_DOMAIN_RESULT_CONTRACT = DomainResultContract(
    contract_id="dev.assayer.frontend-audit.review",
    contract_version="1.0.0",
    check_id="FUA-10",
    check_version="1.1.0",
    result_schema={
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["result", "findings", "reason"],
        "properties": {
            "result": {
                "enum": [
                    "issue_found", "scanned_no_issue", "needs_review",
                    "not_applicable",
                ],
            },
            "findings": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["dimension", "status", "reason"],
                    "properties": {
                        "dimension": {"type": "string", "minLength": 1},
                        "status": {
                            "enum": [
                                "satisfied", "violated", "unresolved",
                                "blocked", "conflicted",
                            ],
                        },
                        "reason": {"type": "string", "minLength": 1},
                        "supportedBy": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
            "reason": {"type": "string", "minLength": 1},
        },
    },
    semantic_instructions_path="assayer_frontend_audit/semantic-review.md",
    semantic_instructions_sha256=hashlib.sha256(_SEMANTIC_REVIEW.read_bytes()).hexdigest(),
)


registration = PluginRegistration(
    FrontendAuditPlugin.manifest,
    plugin_factory=lambda runtime: FrontendAuditPlugin(runtime),
    committer_factory=lambda caller: FrontendDecisionCommitter(caller),
    capabilities=frozenset({"structured_read", "visual_read"}),
    execution_modes=frozenset({"interactive"}),
    scope_schema=FRONTEND_SCOPE_SCHEMA,
    domain_result_contracts=(FRONTEND_DOMAIN_RESULT_CONTRACT,),
)


__all__ = [
    "FrontendAuditPlugin",
    "FrontendDecisionProvider",
    "FrontendDecisionCommitter",
    "FrontendLedgerCommitter",
    "ProductFrontendRuntime",
    "FRONTEND_SCOPE_SCHEMA",
    "FRONTEND_DOMAIN_RESULT_CONTRACT",
    "registration",
]
