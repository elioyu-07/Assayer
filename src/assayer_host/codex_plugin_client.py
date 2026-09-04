"""Restricted Codex CLI adapter for Assayer plugin lifecycle transactions."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

from .lifecycle_transaction import LifecycleCommandError
from .release_lifecycle import PluginInstallation


_MAX_CODEX_JSON_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class CodexCommandResult:
    return_code: int
    stdout: bytes
    stderr: bytes = b""


class CodexCommandRunner(Protocol):
    def run(self, arguments: Sequence[str], *, timeout_seconds: int) -> CodexCommandResult: ...


class SubprocessCodexCommandRunner:
    """Run a fixed Codex executable without a shell or inherited stdin."""

    def __init__(
        self,
        *,
        executable: str = "codex",
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._executable = executable
        self._environment = dict(os.environ if environment is None else environment)

    def run(self, arguments: Sequence[str], *, timeout_seconds: int) -> CodexCommandResult:
        try:
            result = subprocess.run(
                [self._executable, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout_seconds,
                env=self._environment,
            )
        except subprocess.TimeoutExpired as error:
            raise LifecycleCommandError(
                "CODEX_PLUGIN_COMMAND_TIMEOUT",
                "The Codex plugin command exceeded its bounded execution time; reconcile catalog state before retrying.",
            ) from error
        except OSError as error:
            raise LifecycleCommandError(
                "CODEX_CLI_UNAVAILABLE",
                "The Codex CLI could not be started for plugin lifecycle management.",
            ) from error
        return CodexCommandResult(result.returncode, result.stdout, result.stderr)


@dataclass(frozen=True)
class ReleaseAttestation:
    selector: str
    version: str
    available: bool
    release_verified: bool
    immutable_release: bool

    def installation(self, *, installed: bool, enabled: bool) -> PluginInstallation:
        return PluginInstallation(
            self.selector, self.version, installed, enabled, self.available,
            self.release_verified, self.immutable_release,
        )


class CodexPluginClient:
    """Expose only the operations required by PluginLifecycleTransaction."""

    def __init__(
        self,
        runner: CodexCommandRunner,
        *,
        release_attestation: Callable[[str, str | None], ReleaseAttestation | None],
        health_verifier: Callable[[PluginInstallation], bool],
        read_timeout_seconds: int = 30,
        mutation_timeout_seconds: int = 120,
    ) -> None:
        if read_timeout_seconds < 1 or mutation_timeout_seconds < 1:
            raise ValueError("Codex command timeouts must be positive")
        self._runner = runner
        self._release_attestation = release_attestation
        self._health_verifier = health_verifier
        self._read_timeout = read_timeout_seconds
        self._mutation_timeout = mutation_timeout_seconds

    def snapshot(self, selector: str) -> PluginInstallation:
        # Validate before the selector can reach either a command or an error.
        PluginInstallation(selector, None, False, False, False)
        catalog = self._catalog()
        installed = self._matching_entry(catalog.get("installed"), selector)
        available = self._matching_entry(catalog.get("available"), selector)
        installed_version = installed.get("version") if installed is not None else None
        available_version = available.get("version") if available is not None else None
        version_hint = installed_version if isinstance(installed_version, str) else available_version
        version_hint = version_hint if isinstance(version_hint, str) else None
        attestation = self._release_attestation(selector, version_hint)

        if installed is not None:
            version = installed.get("version")
            if not isinstance(version, str):
                raise LifecycleCommandError(
                    "CODEX_PLUGIN_CATALOG_INVALID",
                    "The installed Codex plugin entry has no valid version.",
                )
            facts = self._facts(selector, version, attestation, available is not None)
            return PluginInstallation(
                selector, version, True, installed.get("enabled") is True,
                facts.available, facts.release_verified, facts.immutable_release,
            )

        if available is not None:
            version = available.get("version")
            if not isinstance(version, str):
                raise LifecycleCommandError(
                    "CODEX_PLUGIN_CATALOG_INVALID",
                    "The available Codex plugin entry has no valid version.",
                )
            facts = self._facts(selector, version, attestation, True)
            return facts.installation(installed=False, enabled=False)

        if attestation is not None:
            return attestation.installation(installed=False, enabled=False)
        return PluginInstallation(selector, None, False, False, False)

    def add(self, selector: str) -> None:
        PluginInstallation(selector, None, False, False, False)
        self._mutation(("plugin", "add", selector, "--json"), "CODEX_PLUGIN_ADD_FAILED")

    def remove(self, selector: str) -> None:
        PluginInstallation(selector, None, False, False, False)
        self._mutation(("plugin", "remove", selector, "--json"), "CODEX_PLUGIN_REMOVE_FAILED")

    def verify_installation(self, installation: PluginInstallation) -> bool:
        if not installation.installed or not installation.enabled:
            return False
        live = self.snapshot(installation.selector)
        if (
            not live.installed or not live.enabled or live.version != installation.version
            or not live.release_verified or not live.immutable_release
        ):
            return False
        try:
            return self._health_verifier(live) is True
        except Exception:
            return False

    def _catalog(self) -> dict:
        result = self._runner.run(
            ("plugin", "list", "--json"), timeout_seconds=self._read_timeout,
        )
        payload = self._json_result(result, "CODEX_PLUGIN_LIST_FAILED")
        if not isinstance(payload.get("installed"), list) or not isinstance(payload.get("available"), list):
            raise LifecycleCommandError(
                "CODEX_PLUGIN_CATALOG_INVALID",
                "The Codex plugin catalog does not contain valid installed and available lists.",
            )
        return payload

    def _mutation(self, arguments: Sequence[str], code: str) -> None:
        result = self._runner.run(arguments, timeout_seconds=self._mutation_timeout)
        self._json_result(result, code)

    @staticmethod
    def _json_result(result: CodexCommandResult, failure_code: str) -> dict:
        if len(result.stdout) > _MAX_CODEX_JSON_BYTES:
            raise LifecycleCommandError(
                "CODEX_PLUGIN_OUTPUT_TOO_LARGE",
                "The Codex plugin command returned more JSON than the lifecycle adapter accepts.",
            )
        if result.return_code != 0:
            raise LifecycleCommandError(
                failure_code,
                "The Codex plugin command failed; reconcile the plugin catalog before retrying.",
            )
        try:
            payload = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise LifecycleCommandError(
                "CODEX_PLUGIN_OUTPUT_INVALID",
                "The Codex plugin command did not return a valid JSON object.",
            ) from error
        if not isinstance(payload, dict):
            raise LifecycleCommandError(
                "CODEX_PLUGIN_OUTPUT_INVALID",
                "The Codex plugin command did not return a valid JSON object.",
            )
        return payload

    @staticmethod
    def _matching_entry(value: object, selector: str) -> dict | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise LifecycleCommandError(
                "CODEX_PLUGIN_CATALOG_INVALID",
                "The Codex plugin catalog does not contain valid installed and available lists.",
            )
        matches = [
            item for item in value
            if isinstance(item, dict) and item.get("pluginId") == selector
        ]
        if len(matches) > 1:
            raise LifecycleCommandError(
                "CODEX_PLUGIN_CATALOG_AMBIGUOUS",
                "The Codex plugin catalog contains duplicate entries for one selector.",
            )
        return matches[0] if matches else None

    @staticmethod
    def _facts(
        selector: str,
        version: str,
        attestation: ReleaseAttestation | None,
        catalog_available: bool,
    ) -> ReleaseAttestation:
        if attestation is None or attestation.selector != selector or attestation.version != version:
            return ReleaseAttestation(selector, version, catalog_available, False, False)
        return ReleaseAttestation(
            selector, version, catalog_available or attestation.available,
            attestation.release_verified, attestation.immutable_release,
        )
