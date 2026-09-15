#!/usr/bin/env python3
"""Prepare a clean, path-safe J04/J05 operator-acceptance workspace."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import stat
import sys
from typing import Any
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BASELINE_GATE = "OPR-J04-B01"
BASELINE_PROMPTS = (
    "Install ass-spec from the configured Assayer catalog.",
    "Use ass-spec to review the controlled spec.md input.",
    "Show the conclusion and the full report location.",
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _root_identity(path: Path) -> str:
    return "sha256:" + hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def _stage_marketplace(release: Path, marketplace: Path) -> tuple[str, str]:
    """Stage one exact Codex plugin zip behind a local Marketplace boundary."""
    if release.suffix.lower() != ".zip" or not zipfile.is_zipfile(release):
        raise ValueError("operator release artifact must be a Codex plugin zip")
    with zipfile.ZipFile(release) as bundle:
        files = [item for item in bundle.infolist() if not item.is_dir()]
        if not files:
            raise ValueError("operator release artifact is empty")
        paths = [Path(item.filename) for item in files]
        if any(path.is_absolute() or ".." in path.parts or len(path.parts) < 2 for path in paths):
            raise ValueError("operator release artifact contains an unsafe path")
        roots = {path.parts[0] for path in paths}
        if len(roots) != 1:
            raise ValueError("operator release artifact must contain one plugin root")
        archive_root = next(iter(roots))
        manifest_name = f"{archive_root}/.codex-plugin/plugin.json"
        try:
            manifest = json.loads(bundle.read(manifest_name))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("operator release artifact has no valid plugin manifest") from error
        plugin_name = manifest.get("name")
        plugin_version = manifest.get("version")
        if not isinstance(plugin_name, str) or not plugin_name or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in plugin_name
        ):
            raise ValueError("operator release plugin name is invalid")
        if not isinstance(plugin_version, str) or not plugin_version:
            raise ValueError("operator release plugin version is invalid")
        plugin_root = marketplace / "plugins" / plugin_name
        for item, path in zip(files, paths, strict=True):
            relative = Path(*path.parts[1:])
            target = plugin_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(item))
            mode = (item.external_attr >> 16) & 0o777
            if mode:
                target.chmod(mode)
        marketplace_manifest = {
            "name": "assayer-operator",
            "interface": {"displayName": "Assayer Operator Acceptance"},
            "plugins": [{
                "name": plugin_name,
                "source": {"source": "local", "path": f"./plugins/{plugin_name}"},
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Developer Tools",
            }],
        }
        descriptor = marketplace / ".agents" / "plugins" / "marketplace.json"
        descriptor.parent.mkdir(parents=True, exist_ok=True)
        _write_json(descriptor, marketplace_manifest)
        for executable in ("prepare_assayer_runtime", "launch_assayer_mcp"):
            candidate = plugin_root / "scripts" / executable
            if candidate.is_file():
                candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)
        return plugin_name, plugin_version


def prepare(
    output_root: str | Path,
    *,
    release_artifact: str | Path,
    controlled_input: str | Path,
    source_root: str | Path = ROOT,
    now: datetime | None = None,
    execution_id: str | None = None,
) -> dict[str, Any]:
    """Create one unexecuted operator baseline without inheriting user state."""
    output = Path(output_root).expanduser().resolve()
    source = Path(source_root).expanduser().resolve()
    release = Path(release_artifact).expanduser().resolve()
    audit_input = Path(controlled_input).expanduser().resolve()
    if output == source or _is_within(output, source):
        raise ValueError("operator acceptance output must be outside the source checkout")
    if not release.is_file():
        raise ValueError("release artifact must be an existing file")
    if not audit_input.is_file():
        raise ValueError("controlled input must be an existing file")
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    identifier = execution_id or secrets.token_hex(8)
    if not identifier or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in identifier):
        raise ValueError("execution ID must contain only lowercase letters, digits, and hyphens")
    timestamp = instant.strftime("%Y%m%dT%H%M%SZ")
    execution_name = f"{timestamp}-{identifier}"
    evidence = output / "operator-acceptance" / execution_name
    work = output / ".operator-work" / identifier
    if evidence.exists() or work.exists():
        raise FileExistsError("operator acceptance execution already exists")
    if _is_within(release, evidence) or _is_within(audit_input, evidence):
        raise ValueError("operator evidence directory cannot contain its source inputs")

    artifacts = evidence / "artifacts"
    for directory in (
        artifacts,
        work / "home",
        work / "codex-home",
        work / "xdg-cache",
        work / "plugin-store",
        work / "runs",
    ):
        directory.mkdir(parents=True, exist_ok=False)

    plugin_name, plugin_version = _stage_marketplace(release, work / "marketplace")

    input_suffix = audit_input.suffix if audit_input.suffix else ".txt"
    retained_input = artifacts / f"controlled-input{input_suffix}"
    retained_input.write_bytes(audit_input.read_bytes())
    release_digest = _sha256(release)
    input_digest = _sha256(retained_input)
    environment = {
        "schemaVersion": "1.0.0",
        "executionId": identifier,
        "preparedAt": instant.isoformat().replace("+00:00", "Z"),
        "timezone": "UTC",
        "host": {
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        },
        "releaseCandidate": {
            "name": release.name,
            "sha256": release_digest,
            "pluginName": plugin_name,
            "pluginVersion": plugin_version,
        },
        "profileRootRef": _root_identity(work / "codex-home"),
        "cleanEnvironment": {
            "newCodexHome": True,
            "isolatedHome": True,
            "isolatedCache": True,
            "isolatedPluginStore": True,
            "isolatedRunRoot": True,
            "developmentCheckoutImported": False,
        },
        "executionIdentity": {
            "codexVersion": None,
            "assayerVersion": None,
            "domainPluginVersion": None,
            "recorded": False,
        },
    }
    scenario = {
        "schemaVersion": "1.0.0",
        "executionId": identifier,
        "gateId": BASELINE_GATE,
        "phase": "prepared",
        "controlledInput": {
            "artifact": retained_input.relative_to(evidence).as_posix(),
            "sha256": input_digest,
        },
        "releaseCandidateSha256": release_digest,
        "requiredObservations": [
            "correct_skill_and_tool_routing",
            "bounded_progress_visible",
            "waiting_owner_explicit",
            "terminal_convergence",
            "no_internal_protocol_input",
            "canonical_result_and_report_agree",
        ],
    }
    artifact_index = {
        "schemaVersion": "1.0.0",
        "executionId": identifier,
        "artifacts": [{
            "path": retained_input.relative_to(evidence).as_posix(),
            "mediaType": "text/markdown" if input_suffix.lower() in {".md", ".markdown"} else "text/plain",
            "sha256": input_digest,
            "privacy": "controlled_synthetic",
            "runId": None,
        }],
    }
    launch_environment = {
        "HOME": str(work / "home"),
        "CODEX_HOME": str(work / "codex-home"),
        "XDG_CACHE_HOME": str(work / "xdg-cache"),
        "ASSAYER_PLUGIN_STORE": str(work / "plugin-store"),
        "ASSAYER_OUTPUT_ROOT": str(work / "runs"),
        "ASSAYER_OPERATOR_MARKETPLACE": str(work / "marketplace"),
    }
    _write_json(evidence / "environment.json", environment)
    _write_json(evidence / "scenario.json", scenario)
    _write_json(evidence / "artifact-index.json", artifact_index)
    (evidence / "controlled-prompts.md").write_text(
        "# Controlled Acceptance Prompts\n\n"
        + "\n".join(f"{index}. {prompt}" for index, prompt in enumerate(BASELINE_PROMPTS, 1))
        + "\n",
        encoding="utf-8",
    )
    _write_json(work / "launch-environment.json", launch_environment)
    return {
        "schemaVersion": "1.0.0",
        "status": "prepared",
        "gateId": BASELINE_GATE,
        "executionId": identifier,
        "evidenceRoot": str(evidence),
        "launchEnvironment": str(work / "launch-environment.json"),
        "marketplaceRoot": str(work / "marketplace"),
        "pluginSelector": f"{plugin_name}@assayer-operator",
        "releaseCandidateSha256": release_digest,
        "controlledInputSha256": input_digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--release-artifact", required=True)
    parser.add_argument("--controlled-input", required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(
            args.output_root,
            release_artifact=args.release_artifact,
            controlled_input=args.controlled_input,
        )
    except (FileExistsError, OSError, ValueError) as error:
        result = {
            "schemaVersion": "1.0.0",
            "status": "failed",
            "error": {
                "code": "OPERATOR_ACCEPTANCE_PREPARATION_FAILED",
                "message": str(error),
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
