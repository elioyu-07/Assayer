"""Small, stable helpers for values crossing the plugin boundary."""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
from typing import Any, TypeAlias

from .contract import PlatformContractError


ENTITY_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$"
PLUGIN_CONTRACT_VIOLATION = "PLUGIN_CONTRACT_VIOLATION"

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_ENTITY_ID = re.compile(ENTITY_ID_PATTERN)


class PluginContractError(PlatformContractError):
    """A deterministic plugin-boundary violation safe to return to callers."""

    def __init__(
        self,
        message: str,
        *,
        code: str = PLUGIN_CONTRACT_VIOLATION,
        errors: tuple[Mapping[str, Any], ...] = (),
        work_item_id: str | None = None,
    ) -> None:
        super().__init__(
            code, message, errors=errors, work_item_id=work_item_id,
        )


def validate_entity_id(
    value: Any,
    *,
    label: str = "Entity ID",
    code: str = PLUGIN_CONTRACT_VIOLATION,
    work_item_id: str | None = None,
) -> str:
    """Return a valid platform EntityId or fail at the plugin boundary."""
    if not isinstance(value, str) or _ENTITY_ID.fullmatch(value) is None:
        raise PluginContractError(
            f"{label} must satisfy the platform EntityId contract",
            code=code,
            errors=({
                "pointer": "/",
                "keyword": "entityId",
                "message": "Value does not satisfy the platform EntityId contract",
            },),
            work_item_id=work_item_id,
        )
    return value


def _pointer_child(pointer: str, part: str | int) -> str:
    token = str(part).replace("~", "~0").replace("/", "~1")
    return f"{pointer}/{token}" if pointer else f"/{token}"


def _canonical_sort_key(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def to_json_value(
    value: Any,
    *,
    label: str = "Plugin value",
    code: str = PLUGIN_CONTRACT_VIOLATION,
    work_item_id: str | None = None,
) -> JsonValue:
    """Detach a recursively JSON-safe value or reject it without coercion.

    Mapping keys must already be strings. Sets are accepted for immutable
    platform contract values and are projected into a deterministic list.
    Unknown objects, bytes, non-finite floats, and cyclic containers fail fast.
    """

    active_containers: set[int] = set()

    def fail(pointer: str, keyword: str, detail: str) -> None:
        location = pointer or "/"
        raise PluginContractError(
            f"{label} must contain only JSON-compatible values at {location}",
            code=code,
            errors=({
                "pointer": location,
                "keyword": keyword,
                "message": detail,
            },),
            work_item_id=work_item_id,
        )

    def convert(item: Any, pointer: str) -> JsonValue:
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                fail(pointer, "finiteNumber", "Number must be finite")
            return item

        is_mapping = isinstance(item, Mapping)
        is_sequence = isinstance(item, (list, tuple))
        is_set = isinstance(item, (set, frozenset))
        if not (is_mapping or is_sequence or is_set):
            fail(pointer, "type", "Value must be a JSON-compatible type")

        identity = id(item)
        if identity in active_containers:
            fail(pointer, "acyclic", "Container values must not be cyclic")
        active_containers.add(identity)
        try:
            if is_mapping:
                result: dict[str, JsonValue] = {}
                for key, nested in item.items():
                    if not isinstance(key, str):
                        fail(pointer, "propertyNames", "Object keys must be strings")
                    result[key] = convert(nested, _pointer_child(pointer, key))
                return result
            if is_sequence:
                return [
                    convert(nested, _pointer_child(pointer, index))
                    for index, nested in enumerate(item)
                ]
            converted = [
                convert(nested, _pointer_child(pointer, index))
                for index, nested in enumerate(item)
            ]
            return sorted(converted, key=_canonical_sort_key)
        finally:
            active_containers.remove(identity)

    return convert(value, "")


__all__ = [
    "ENTITY_ID_PATTERN",
    "JsonScalar",
    "JsonValue",
    "PLUGIN_CONTRACT_VIOLATION",
    "PluginContractError",
    "to_json_value",
    "validate_entity_id",
]
