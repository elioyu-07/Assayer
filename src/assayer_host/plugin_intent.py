"""Deterministic natural-language intent resolution for the plugin workbench.

Layer 1 of the agent-first plugin lifecycle: a user's natural-language request
is resolved into a deterministic plan of lifecycle operations.  Resolution is
pure keyword/pattern matching (no LLM); when an intent cannot be resolved to a
complete operation sequence it fails closed by raising
:class:`IntentResolutionError` so the caller can ask a clarifying question
rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DANGEROUS = frozenset({"install", "upgrade", "downgrade", "rollback", "uninstall"})

_STOPWORDS = frozenset({
    "the", "a", "an", "to", "of", "for", "with", "my", "me", "our", "please",
    "and", "at", "it", "this", "that", "version", "plugin", "plugins", "on",
})

_VERB_WORDS = frozenset({
    "install", "add", "upgrade", "update", "downgrade", "pin", "rollback",
    "roll", "back", "undo", "uninstall", "remove", "delete", "get", "rid",
    "list", "show", "info", "about", "tell", "describe", "run", "review",
    "audit", "check",
})

_SEMVER = re.compile(r"\b\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\b")
_CHECK_ID = re.compile(r"\b[A-Z][A-Z0-9]*-[0-9]+\b")


class IntentResolutionError(Exception):
    """An intent could not be deterministically resolved to an operation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IntentStep:
    operation: str
    plugin_id: str | None = None
    version: str | None = None
    package: str | None = None
    check_id: str | None = None
    scope_file: str | None = None

    @property
    def dangerous(self) -> bool:
        return self.operation in DANGEROUS

    def as_dict(self) -> dict:
        value = {"operation": self.operation}
        if self.plugin_id is not None:
            value["pluginId"] = self.plugin_id
        if self.version is not None:
            value["version"] = self.version
        if self.package is not None:
            value["package"] = self.package
        if self.check_id is not None:
            value["checkId"] = self.check_id
        if self.scope_file is not None:
            value["scopeFile"] = self.scope_file
        return value


def _strip(text: str) -> str:
    return text.replace(",", " ").replace(";", " ").replace('"', " ").replace("'", " ")


def _contains(lowered: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        if re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", lowered):
            return True
    return False


def _resolve_plugin_id(lowered: str, known_ids: tuple[str, ...]) -> str | None:
    for plugin_id in sorted(known_ids, key=len, reverse=True):
        if plugin_id.lower() in lowered:
            return plugin_id
    for plugin_id in sorted(known_ids, key=lambda i: len(i.rsplit(".", 1)[-1]), reverse=True):
        short = plugin_id.rsplit(".", 1)[-1].lower()
        if re.search(r"(?<![a-z0-9])" + re.escape(short) + r"(?![a-z0-9])", lowered):
            return plugin_id
    return None


def _extract_version(text: str) -> str | None:
    match = _SEMVER.search(text)
    return match.group(0) if match else None


def _extract_check_id(text: str) -> str | None:
    match = _CHECK_ID.search(text)
    return match.group(0) if match else None


def _extract_file(text: str, cwd: Path | None) -> str | None:
    for token in _strip(text).split():
        path = Path(token).expanduser()
        if path.is_file():
            return str(path.resolve())
        if cwd is not None and (cwd / token).expanduser().is_file():
            return str((cwd / token).expanduser().resolve())
    return None


def _extract_package(text: str, cwd: Path | None) -> str | None:
    candidates = [
        token for token in _strip(text).split()
        if token.lower() not in _STOPWORDS
        and token.lower() not in _VERB_WORDS
        and _SEMVER.fullmatch(token) is None
    ]
    for token in candidates:
        path = Path(token).expanduser()
        if path.is_dir():
            return str(path.resolve())
        if cwd is not None and (cwd / token).expanduser().is_dir():
            return str((cwd / token).expanduser().resolve())
    return candidates[0] if candidates else None


def resolve_intent(
    text: str,
    *,
    known_plugin_ids: tuple[str, ...] = (),
    cwd: Path | None = None,
) -> tuple[IntentStep, ...]:
    if not text or not text.strip():
        raise IntentResolutionError("INTENT_EMPTY", "Provide a natural-language request.")

    lowered = _strip(text).lower()

    if _contains(lowered, ("rollback", "roll back", "undo")):
        operation = "rollback"
    elif _contains(lowered, ("downgrade", "pin")):
        operation = "downgrade"
    elif _contains(lowered, ("uninstall", "remove", "delete", "get rid of")):
        operation = "uninstall"
    elif _contains(lowered, ("upgrade", "update")):
        operation = "upgrade"
    elif _contains(lowered, ("install", "add")):
        operation = "install"
    elif _contains(lowered, ("tell me about", "about", "info", "describe")):
        operation = "info"
    elif "what plugin" in lowered or _contains(lowered, ("list", "show")):
        operation = "list"
    elif _contains(lowered, ("run", "review", "audit", "check")):
        operation = "run"
    else:
        raise IntentResolutionError(
            "INTENT_UNRESOLVED",
            "Could not resolve the request to a plugin operation.",
        )

    if operation == "list":
        return (IntentStep("list"),)

    plugin_id = _resolve_plugin_id(lowered, known_plugin_ids)

    if operation == "info":
        if plugin_id is None:
            raise IntentResolutionError(
                "INTENT_NEEDS_PLUGIN",
                "Specify which plugin (e.g. 'tell me about touchstone').",
            )
        return (IntentStep("info", plugin_id=plugin_id),)

    if operation in {"install", "upgrade"}:
        package = _extract_package(text, cwd)
        if package is None or not Path(package).expanduser().is_dir():
            raise IntentResolutionError(
                "INTENT_PACKAGE_UNRESOLVED",
                f"{operation} needs a local package directory as its source.",
            )
        return (IntentStep(operation, package=str(Path(package).expanduser().resolve())),)

    if operation == "downgrade":
        version = _extract_version(text)
        if plugin_id is None or version is None:
            raise IntentResolutionError(
                "INTENT_NEEDS_DETAIL",
                "downgrade needs a plugin and a target version (e.g. 'pin touchstone to 1.0.0').",
            )
        return (IntentStep("downgrade", plugin_id=plugin_id, version=version),)

    if operation == "rollback":
        if plugin_id is None:
            raise IntentResolutionError(
                "INTENT_NEEDS_PLUGIN",
                "rollback needs a plugin (e.g. 'rollback touchstone').",
            )
        return (IntentStep("rollback", plugin_id=plugin_id),)

    if operation == "uninstall":
        if plugin_id is None:
            raise IntentResolutionError(
                "INTENT_NEEDS_PLUGIN",
                "remove/uninstall needs a plugin (e.g. 'remove touchstone').",
            )
        return (IntentStep("uninstall", plugin_id=plugin_id),)

    check_id = _extract_check_id(text)
    scope_file = _extract_file(text, cwd)
    if plugin_id is None or check_id is None or scope_file is None:
        raise IntentResolutionError(
            "INTENT_NEEDS_DETAIL",
            "run needs a plugin, a check id, and a scope file "
            "(e.g. 'run touchstone SPEC-001 on spec.md').",
        )
    return (IntentStep("run", plugin_id=plugin_id, check_id=check_id, scope_file=scope_file),)


__all__ = ["IntentStep", "IntentResolutionError", "resolve_intent"]
