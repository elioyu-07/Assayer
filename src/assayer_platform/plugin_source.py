"""Safe, deterministic handling of local plugin source trees.

Local repositories are authoring inputs, never installation artifacts.  This
module defines the one source view used both to bind a lifecycle plan and to
stage an isolated wheel build.  Transient repository and build state is
excluded from that view, while every potentially release-relevant source file
remains covered by the digest.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .contract import PlatformContractError


_IGNORED_DIRECTORIES = frozenset({
    ".assayer",
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
})
_IGNORED_FILES = frozenset({".DS_Store"})


def _ignored(relative: Path) -> bool:
    return (
        any(part in _IGNORED_DIRECTORIES or part.endswith(".egg-info") for part in relative.parts)
        or relative.name in _IGNORED_FILES
        or relative.suffix in {".pyc", ".pyo"}
    )


def _source_files(root: Path) -> tuple[tuple[Path, Path], ...]:
    files: list[tuple[Path, Path]] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if _ignored(relative):
            continue
        if path.is_symlink():
            raise PlatformContractError(
                "PLUGIN_SOURCE_UNSAFE",
                f"Local plugin source contains a symbolic link: {relative.as_posix()}",
            )
        if path.is_file():
            files.append((relative, path))
    files.sort(key=lambda item: item[0].as_posix())
    return tuple(files)


def source_tree_checksum(source: str | Path) -> str:
    """Hash the exact sanitized source view used by the wheel builder."""
    root = Path(source).expanduser().resolve()
    if not root.is_dir():
        raise PlatformContractError(
            "PLUGIN_SOURCE_UNREACHABLE",
            f"The plugin source is unreachable or does not exist: {root}",
        )
    digest = hashlib.sha256()
    for relative, path in _source_files(root):
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError as error:
            raise PlatformContractError(
                "PLUGIN_SOURCE_UNREACHABLE",
                f"The plugin source could not be read: {relative.as_posix()}: {error}",
            ) from error
    return digest.hexdigest()


def stage_plugin_source(source: str | Path, destination: str | Path) -> Path:
    """Copy only the plan-bound source view into an isolated build directory."""
    root = Path(source).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    files = _source_files(root)
    target.mkdir(parents=True, exist_ok=True)
    for relative, path in files:
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, output)
    return target


__all__ = ["source_tree_checksum", "stage_plugin_source"]
