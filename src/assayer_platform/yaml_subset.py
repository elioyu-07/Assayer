"""Small dependency-free YAML subset for ordinary plugin declarations.

Supported syntax is intentionally narrow: indentation-based objects and
arrays, scalar values, inline scalar arrays, comments, and literal blocks.
Aliases, tags, merge keys, and executable constructors are rejected.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from .contract import PlatformContractError


_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return None
    lowered = text.casefold()
    if lowered in {"null", "~"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    if text.startswith("[") and text.endswith("]"):
        body = text[1:-1].strip()
        if not body:
            return []
        return [_scalar(item) for item in body.split(",")]
    try:
        parsed_json = json.loads(text)
    except (ValueError, TypeError):
        parsed_json = None
    if isinstance(parsed_json, (str, int, float)) and not isinstance(parsed_json, bool):
        return parsed_json
    if text[:1] in {"'", '"'}:
        try:
            value = ast.literal_eval(text)
        except (SyntaxError, ValueError) as error:
            raise PlatformContractError(
                "POLICY_YAML_INVALID", "Quoted YAML scalar is malformed",
            ) from error
        if not isinstance(value, str):
            raise PlatformContractError(
                "POLICY_YAML_INVALID", "Only string quoted scalars are supported",
            )
        return value
    return text


def _pair(text: str) -> tuple[str, str]:
    key, separator, rest = text.partition(":")
    if not separator or _KEY.fullmatch(key.strip()) is None:
        raise PlatformContractError(
            "POLICY_YAML_INVALID", f"Invalid YAML mapping entry: {text}",
        )
    return key.strip(), rest.strip()


def loads_yaml_subset(source: str) -> Any:
    if not isinstance(source, str):
        raise PlatformContractError("POLICY_YAML_INVALID", "YAML source must be text")
    lines: list[tuple[int, str, int]] = []
    raw_lines = source.splitlines()
    for number, raw in enumerate(raw_lines, 1):
        if "\t" in raw:
            raise PlatformContractError(
                "POLICY_YAML_INVALID", f"YAML tabs are forbidden at line {number}",
            )
        stripped = raw.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(stripped)
        lines.append((indent, stripped.rstrip(), number))
    if not lines:
        raise PlatformContractError("POLICY_YAML_INVALID", "YAML document is empty")

    def parse(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines) or lines[index][0] != indent:
            raise PlatformContractError("POLICY_YAML_INVALID", "YAML indentation is invalid")
        is_list = lines[index][1].startswith("- ") or lines[index][1] == "-"
        result: Any = [] if is_list else {}
        while index < len(lines) and lines[index][0] == indent:
            _line_indent, content, number = lines[index]
            if is_list:
                if not (content.startswith("- ") or content == "-"):
                    break
                rest = content[1:].strip()
                index += 1
                if not rest:
                    if index >= len(lines) or lines[index][0] <= indent:
                        raise PlatformContractError(
                            "POLICY_YAML_INVALID", f"Empty YAML list item at line {number}",
                        )
                    item, index = parse(index, lines[index][0])
                elif ":" in rest:
                    key, raw_value = _pair(rest)
                    item = {}
                    if raw_value:
                        item[key] = _scalar(raw_value)
                    elif index < len(lines) and lines[index][0] > indent:
                        item[key], index = parse(index, lines[index][0])
                    else:
                        item[key] = None
                    if index < len(lines) and lines[index][0] > indent:
                        continuation_indent = lines[index][0]
                        continuation, index = parse(index, continuation_indent)
                        if not isinstance(continuation, dict):
                            raise PlatformContractError(
                                "POLICY_YAML_INVALID",
                                f"YAML list mapping continuation is invalid at line {number}",
                            )
                        duplicate = set(item).intersection(continuation)
                        if duplicate:
                            raise PlatformContractError(
                                "POLICY_YAML_INVALID", "YAML mapping keys must be unique",
                            )
                        item.update(continuation)
                else:
                    item = _scalar(rest)
                result.append(item)
            else:
                if content.startswith("-"):
                    break
                key, raw_value = _pair(content)
                if key in result:
                    raise PlatformContractError(
                        "POLICY_YAML_INVALID", f"Duplicate YAML key at line {number}: {key}",
                    )
                index += 1
                if raw_value in {"|", ">"}:
                    block: list[str] = []
                    while index < len(lines) and lines[index][0] > indent:
                        block.append(lines[index][1])
                        index += 1
                    result[key] = ("\n" if raw_value == "|" else " ").join(block)
                elif raw_value:
                    result[key] = _scalar(raw_value)
                elif index < len(lines) and lines[index][0] > indent:
                    result[key], index = parse(index, lines[index][0])
                else:
                    result[key] = None
        return result, index

    value, end = parse(0, lines[0][0])
    if end != len(lines):
        raise PlatformContractError(
            "POLICY_YAML_INVALID", f"Unexpected YAML structure at line {lines[end][2]}",
        )
    return value


def load_yaml_subset(path: str | Path) -> Any:
    source = Path(path)
    try:
        return loads_yaml_subset(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise PlatformContractError(
            "POLICY_SOURCE_UNAVAILABLE", f"Cannot read policy source: {source}",
        ) from error


__all__ = ["load_yaml_subset", "loads_yaml_subset"]
