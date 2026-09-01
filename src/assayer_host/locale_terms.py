"""Multilingual target-page terms used by fixed browser probes.

These values are input-recognition data, not user-facing Assayer copy. Keep
product and engineering prose in English while extending this module when a
new target-page locale must be recognized.
"""

from __future__ import annotations

import json


RESET_ACTION_LABELS = (
    "reset",
    "clear",
    "\u91cd\u7f6e",
    "\u6e05\u7a7a",
    "\u6062\u590d\u9ed8\u8ba4",
)

QUERY_ACTION_LABELS = (
    "query",
    "search",
    "filter",
    "\u67e5\u8be2",
    "\u641c\u7d22",
    "\u7b5b\u9009",
)

WRITE_ACTION_LABELS = (
    "save", "submit", "delete", "remove", "approve", "publish", "upload", "import", "cancel", "unbind",
    "\u4f5c\u5e9f", "\u5220\u9664", "\u4fdd\u5b58", "\u63d0\u4ea4", "\u5ba1\u6279", "\u53d1\u5e03", "\u4e0a\u4f20", "\u5bfc\u5165",
)


def inject_action_labels(script: str) -> str:
    """Inject JSON-escaped locale data into a fixed JavaScript probe."""
    return (
        script.replace("__ASSAYER_RESET_ACTION_LABELS__", json.dumps(RESET_ACTION_LABELS, ensure_ascii=True))
        .replace("__ASSAYER_QUERY_ACTION_LABELS__", json.dumps(QUERY_ACTION_LABELS, ensure_ascii=True))
    )
