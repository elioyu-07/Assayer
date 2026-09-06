"""Assemble a self-contained, distributable plugin wheel.

A distributed plugin is a single wheel whose root carries the release
descriptor and every non-code resource it references.  ``uv build`` produces a
standard code wheel but leaves the root-level release descriptor, fixtures,
semantic review, and ``pyproject.toml`` behind.  This module closes that gap:

* :func:`wheel_descriptor` rewrites a source-tree descriptor into wheel-relative
  paths (``runtimeSource`` collapses to ``.`` and the source prefix is dropped).
* :func:`assemble_self_contained_wheel` injects the descriptor and resources
  into a built wheel and regenerates ``*.dist-info/RECORD`` so the result stays
  a well-formed, integrity-checked wheel.

Everything here is pure and side-effect free so it can be unit tested without
building a real package.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

from .conformance import RELEASE_DESCRIPTOR
from .contract import PlatformContractError


def _record_digest(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def wheel_descriptor(source_descriptor: dict) -> dict:
    """Rewrite a source-tree release descriptor into wheel-relative paths."""
    result = dict(source_descriptor)
    runtime_source = source_descriptor.get("runtimeSource", ".")
    if runtime_source not in {"", ".", "./"}:
        for key in ("manifest", "scopeSchema"):
            value = source_descriptor.get(key)
            if isinstance(value, str) and value.startswith(runtime_source.rstrip("/") + "/"):
                result[key] = value[len(runtime_source):].lstrip("/")
    result["runtimeSource"] = "."
    return result


def assemble_self_contained_wheel(
    wheel_bytes: bytes,
    source_root: str | Path,
    source_descriptor: dict,
) -> bytes:
    """Inject descriptor + resources into a built wheel, regenerating RECORD."""
    root = Path(source_root).expanduser().resolve()
    descriptor = wheel_descriptor(source_descriptor)
    injected: dict[str, bytes] = {
        RELEASE_DESCRIPTOR: json.dumps(descriptor, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
    }
    for key in ("semanticReview", "packageMetadata"):
        value = descriptor.get(key)
        if not isinstance(value, str):
            continue
        resource = root / value
        if resource.is_file():
            injected[value] = resource.read_bytes()
    for fixture in descriptor.get("fixtures", []):
        resource = root / fixture
        if resource.is_file():
            injected[fixture] = resource.read_bytes()

    records: list[tuple[str, str]] = []
    entries: dict[str, bytes] = {}

    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as source:
        for name in source.namelist():
            if name.endswith(".dist-info/RECORD"):
                continue
            entries[name] = source.read(name)

    for name, content in injected.items():
        entries[name] = content

    for name in sorted(entries):
        records.append(f"{name},{_record_digest(entries[name])},{len(entries[name])}")
    record_dir = None
    for name in entries:
        if ".dist-info/" in name:
            record_dir = name.split(".dist-info/", 1)[0] + ".dist-info/"
            break
    if record_dir is None:
        raise PlatformContractError(
            "PLUGIN_PACKAGE_INVALID",
            "The built wheel has no *.dist-info directory to host its RECORD.",
        )
    record_path = record_dir + "RECORD"
    records.append(f"{record_path},,")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            archive.writestr(name, entries[name])
        archive.writestr(record_path, ("\n".join(records) + "\n").encode("utf-8"))
    return buffer.getvalue()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
