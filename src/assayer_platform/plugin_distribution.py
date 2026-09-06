"""Download-side materialization for distributed plugin wheels.

A distributed plugin is a single downloadable archive (a wheel) whose contract
is: extract it and ``assayer-plugin-release.json`` sits at the extraction root,
with every other resource referenced relative to it.  This module owns the two
fail-closed steps that happen *before* the existing store install runs: digest
verification and safe extraction.

It is deliberately free of network I/O so it can be tested without a socket;
the caller is responsible for downloading bytes and passing them here.
"""

from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .conformance import RELEASE_DESCRIPTOR
from .contract import PlatformContractError


def verify_sha256(data: bytes, expected: str) -> None:
    """Fail closed when the downloaded bytes do not match the catalog digest."""
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected.lower():
        raise PlatformContractError(
            "PLUGIN_CHECKSUM_MISMATCH",
            f"The downloaded plugin digest {digest} does not match the catalog "
            f"digest {expected.lower()}.",
        )


def _unsafe(member: str, reason: str) -> PlatformContractError:
    return PlatformContractError(
        "PLUGIN_ARCHIVE_UNSAFE",
        f"The plugin archive contains an unsafe entry {member!r}: {reason}",
    )


def extract_archive(data: bytes, target: Path) -> None:
    """Extract a wheel (a zip) into ``target``, rejecting traversal and symlinks."""
    root = Path(target).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            member = info.filename
            path = PurePosixPath(member)
            if path.is_absolute() or ".." in path.parts:
                raise _unsafe(member, "path is absolute or escapes the package root")
            if "\\" in member or member.startswith("/"):
                raise _unsafe(member, "path uses an unsafe separator")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise _unsafe(member, "symbolic links are not permitted")
            destination = root.joinpath(*path.parts)
            resolved = destination.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                raise _unsafe(member, "path resolves outside the package root")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info))


def materialize_wheel(data: bytes, expected_sha256: str, target: Path) -> Path:
    """Verify and extract a wheel, returning its package root.

    The returned directory contains ``assayer-plugin-release.json`` at its root
    and is a drop-in argument for :meth:`PluginLifecycleManager.install`.
    """
    verify_sha256(data, expected_sha256)
    extract_archive(data, target)
    root = Path(target).expanduser().resolve()
    if not (root / RELEASE_DESCRIPTOR).is_file():
        raise PlatformContractError(
            "PLUGIN_PACKAGE_NOT_FOUND",
            "The plugin archive does not contain a release descriptor at its root.",
        )
    return root
