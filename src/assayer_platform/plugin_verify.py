"""Deterministic verification for the one data-only ordinary-plugin contract."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tempfile
from typing import Any

from .compiled_plugin_contract import (
    COMPILED_PLUGIN_CONTRACT,
    load_compiled_plugin_contract,
)
from .contract import PlatformContractError
from .declaration_compiler import compile_plugin_contract


def _failure(code: str, message: str, stages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "status": "failed",
        "error": {"code": code, "message": message},
        "stages": stages,
    }


def verify_plugin_source(
    source: str | Path, *, output_dir: str | Path,
) -> dict[str, Any]:
    """Compile and verify one declaration-only plugin without importing code."""
    source_root = Path(source).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    if not source_root.is_dir():
        return _failure(
            "PLUGIN_SOURCE_UNREACHABLE",
            f"The plugin source is unreachable or does not exist: {source_root}",
            [{"name": "source", "status": "failed"}],
        )
    if not (source_root / "plugin.yaml").is_file():
        code = (
            "ADVANCED_SPI_REMOVED"
            if (source_root / "assayer-plugin-release.json").is_file()
            else "PLUGIN_DECLARATION_NOT_FOUND"
        )
        return _failure(
            code,
            "Only declaration-only ordinary plugins are accepted; plugin.yaml is required.",
            [{"name": "source", "status": "failed"}],
        )

    stages: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="assayer-plugin-contract-") as directory:
        generated = Path(directory) / "generated"
        try:
            compiled = compile_plugin_contract(source_root, generated)
        except PlatformContractError as error:
            return _failure(
                error.code, error.message,
                [{"name": "compile", "status": "failed"}],
            )
        stages.append({"name": "compile", "status": "passed"})
        try:
            contract = load_compiled_plugin_contract(generated)
        except PlatformContractError as error:
            return _failure(
                error.code, error.message,
                [*stages, {"name": "contract_validation", "status": "failed"}],
            )
        stages.append({"name": "contract_validation", "status": "passed"})
        source_artifact = generated / COMPILED_PLUGIN_CONTRACT
        exact_bytes = source_artifact.read_bytes()

    destination.mkdir(parents=True, exist_ok=True)
    artifact_name = re.sub(r"[^A-Za-z0-9._-]", "-", contract.plugin_id)
    artifact = destination / f"{artifact_name}-{contract.version}.assayer-plugin.json"
    artifact.write_bytes(exact_bytes)
    digest = hashlib.sha256(exact_bytes).hexdigest()
    return {
        "schemaVersion": "1.0.0",
        "status": "passed",
        "pluginId": compiled.plugin_id,
        "pluginVersion": compiled.version,
        "contractDigest": contract.digest,
        "artifact": str(artifact),
        "sha256": digest,
        "stages": stages,
        "acceptance": {
            "dataOnly": True,
            "pluginCodeImported": False,
            "businessCasesPresent": bool(contract.payload["cases"]),
        },
    }


__all__ = ["verify_plugin_source"]
