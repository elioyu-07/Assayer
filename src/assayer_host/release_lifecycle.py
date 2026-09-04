"""Read-only planning contract for safe Assayer plugin lifecycle changes."""

from __future__ import annotations

import re
from dataclasses import dataclass


_SELECTOR = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,63}@[a-z0-9][a-z0-9._-]{0,63}$"
)
_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_OPERATIONS = {"upgrade", "rollback", "uninstall"}


@dataclass(frozen=True)
class PluginInstallation:
    """Sanitized plugin state obtained from the Codex plugin catalog."""

    selector: str
    version: str | None
    installed: bool
    enabled: bool
    available: bool
    release_verified: bool = False
    immutable_release: bool = False

    def __post_init__(self) -> None:
        if not _SELECTOR.fullmatch(self.selector):
            raise ValueError("selector must use the plugin@marketplace form")
        if self.version is not None and not _VERSION.fullmatch(self.version):
            raise ValueError("version must be a semantic version")
        if self.installed and self.version is None:
            raise ValueError("an installed plugin must have a version")

    def as_dict(self) -> dict:
        return {
            "selector": self.selector,
            "version": self.version,
            "installed": self.installed,
            "enabled": self.enabled,
            "available": self.available,
            "releaseVerified": self.release_verified,
            "immutableRelease": self.immutable_release,
        }


class PluginLifecyclePlanner:
    """Produce a fail-closed plan; this class never runs a Codex command."""

    @staticmethod
    def _precondition(code: str, passed: bool, message: str) -> dict:
        return {
            "code": code,
            "status": "pass" if passed else "fail",
            "message": message,
        }

    def build(
        self,
        operation: str,
        current: PluginInstallation,
        *,
        target: PluginInstallation | None = None,
        active_run: bool = False,
        release_direction_verified: bool = False,
    ) -> dict:
        if operation not in _OPERATIONS:
            raise ValueError("operation must be upgrade, rollback, or uninstall")
        preconditions = [self._precondition(
            "NO_ACTIVE_RUN", not active_run,
            "No Assayer Run is active." if not active_run else
            "An Assayer Run is active; lifecycle changes are not safe.",
        )]

        if operation == "uninstall":
            return self._uninstall_plan(operation, current, preconditions)
        return self._replacement_plan(
            operation, current, target, release_direction_verified, preconditions,
        )

    def _uninstall_plan(
        self, operation: str, current: PluginInstallation,
        preconditions: list[dict],
    ) -> dict:
        if not current.installed:
            preconditions.append(self._precondition(
                "CURRENT_ALREADY_ABSENT", True,
                "The selected Assayer plugin is already absent.",
            ))
            if preconditions[0]["status"] == "pass":
                return self._plan(
                    operation, "noop", current, None, preconditions, (), (),
                    rollback_ready=False,
                    rollback_reason="No mutation is required because the plugin is already absent.",
                    next_action="No uninstall action is required.",
                )
            return self._plan(
                operation, "blocked", current, None, preconditions, (), (),
                rollback_ready=False,
                rollback_reason="No mutation is required, but lifecycle planning is blocked by the active Run.",
                next_action="Finish or stop the active Run before lifecycle cleanup.",
            )
        preconditions.append(self._precondition(
            "CURRENT_INSTALLATION_IDENTIFIED", True,
            "The installed Assayer plugin is identified.",
        ))
        preconditions.append(self._precondition(
            "CURRENT_RELEASE_RESTORABLE",
            current.available and current.release_verified and current.immutable_release,
            "The current release has an immutable, verified source for compensation."
            if current.available and current.release_verified and current.immutable_release else
            "The current release has no immutable, verified source for compensation.",
        ))
        ready = all(item["status"] == "pass" for item in preconditions)
        steps = (
            self._step(1, "verify_preconditions", "Re-read the Codex plugin catalog and active-Run guard."),
            self._step(2, "remove_current", "Remove the exact current selector through Codex plugin remove."),
            self._step(3, "verify_absent", "Verify that the exact selector is absent from the Codex plugin catalog."),
        ) if ready else ()
        compensation = (
            self._step(1, "restore_current", "Reinstall the immutable current selector if uninstall verification fails."),
            self._step(2, "verify_restored", "Verify the restored selector, version, enabled state, and installation status."),
        ) if ready else ()
        return self._plan(
            operation, "ready" if ready else "blocked", current, None,
            preconditions, steps, compensation, rollback_ready=ready,
            rollback_reason=(
                "Uninstall intentionally removes the current installation; cleanup must not begin until absence is verified."
            ),
            next_action=(
                "Request explicit user confirmation, then execute this exact uninstall plan."
                if ready else "Resolve every failed precondition before uninstalling Assayer."
            ),
        )

    def _replacement_plan(
        self,
        operation: str,
        current: PluginInstallation,
        target: PluginInstallation | None,
        release_direction_verified: bool,
        preconditions: list[dict],
    ) -> dict:
        preconditions.append(self._precondition(
            "CURRENT_INSTALLATION_IDENTIFIED", current.installed and current.enabled,
            "The current Assayer installation is installed and enabled."
            if current.installed and current.enabled else
            "The current Assayer installation is absent or disabled.",
        ))
        if target is None:
            preconditions.append(self._precondition(
                "TARGET_RELEASE_IDENTIFIED", False,
                "No target release was identified.",
            ))
            return self._blocked_replacement(operation, current, None, preconditions)

        current_name = current.selector.split("@", 1)[0]
        target_name = target.selector.split("@", 1)[0]
        preconditions.extend((
            self._precondition(
                "TARGET_SELECTOR_DISTINCT", target.selector != current.selector,
                "The target uses a distinct immutable marketplace selector."
                if target.selector != current.selector else
                "The target selector is the current mutable selector and cannot be staged safely.",
            ),
            self._precondition(
                "PLUGIN_IDENTITY_PRESERVED", current_name == target_name,
                "The current and target selectors identify the same plugin name."
                if current_name == target_name else
                "The target selector identifies a different plugin name.",
            ),
            self._precondition(
                "TARGET_RELEASE_AVAILABLE", target.available or target.installed,
                "The target release is available to Codex."
                if target.available or target.installed else
                "The target release is not available to Codex.",
            ),
            self._precondition(
                "TARGET_RELEASE_VERIFIED", target.release_verified,
                "The target release passed the Assayer package and installation conformance gates."
                if target.release_verified else
                "The target release has no accepted Assayer conformance record.",
            ),
            self._precondition(
                "TARGET_RELEASE_IMMUTABLE", target.immutable_release,
                "The target release is addressed through an immutable marketplace source."
                if target.immutable_release else
                "The target release uses a mutable source and cannot support deterministic rollback.",
            ),
            self._precondition(
                "CURRENT_RELEASE_RESTORABLE",
                current.available and current.release_verified and current.immutable_release,
                "The current release has an immutable, verified source for compensation."
                if current.available and current.release_verified and current.immutable_release else
                "The current release has no immutable, verified source for compensation.",
            ),
            self._precondition(
                "RELEASE_DIRECTION_VERIFIED", release_direction_verified,
                f"The release catalog confirms this {operation} direction."
                if release_direction_verified else
                f"The release catalog has not confirmed this {operation} direction.",
            ),
            self._precondition(
                "TARGET_VERSION_DISTINCT",
                current.version is not None and target.version is not None and current.version != target.version,
                "The target version differs from the current version."
                if current.version is not None and target.version is not None and current.version != target.version else
                "The target version is missing or is the same as the current version.",
            ),
            self._precondition(
                "TARGET_NOT_INSTALLED", not target.installed,
                "The target is not installed in the active Codex configuration."
                if not target.installed else
                "The target is already installed and could contribute duplicate Skill or MCP names.",
            ),
        ))
        ready = all(item["status"] == "pass" for item in preconditions)
        if not ready:
            return self._blocked_replacement(operation, current, target, preconditions)

        steps = [self._step(
            1, "verify_preconditions",
            "Re-read the Codex plugin catalog, conformance receipt, release direction, and active-Run guard.",
        )]
        steps.append(self._step(
            2, "remove_current",
            "Remove the exact current selector through Codex plugin remove after both immutable release sources are verified.",
        ))
        steps.append(self._step(
            3, "install_target",
            "Install the distinct immutable target selector through Codex plugin add.",
        ))
        steps.append(self._step(
            4, "verify_target",
            "Verify the target selector, exact version, enabled state, and Assayer installation status.",
        ))
        steps.append(self._step(
            5, "verify_terminal_state",
            "Verify that the target remains healthy and the old selector is absent.",
        ))
        compensation = (
            self._step(1, "remove_failed_target", "Remove the target selector if it was partially installed."),
            self._step(2, "restore_current", "Reinstall the immutable previous selector through Codex plugin add."),
            self._step(3, "verify_restored", "Verify the restored selector, version, enabled state, and installation status."),
        )
        return self._plan(
            operation, "ready", current, target, preconditions, steps, compensation,
            rollback_ready=True,
            rollback_reason=(
                "The previous immutable release remains available and has a complete compensation sequence."
            ),
            next_action=f"Request explicit user confirmation, then execute this exact {operation} plan.",
        )

    def _blocked_replacement(
        self,
        operation: str,
        current: PluginInstallation,
        target: PluginInstallation | None,
        preconditions: list[dict],
    ) -> dict:
        return self._plan(
            operation, "blocked", current, target, preconditions, (), (),
            rollback_ready=False,
            rollback_reason="No mutation may start until every replacement precondition passes.",
            next_action=f"Resolve every failed precondition before executing the {operation}.",
        )

    @staticmethod
    def _step(sequence: int, action: str, message: str) -> dict:
        return {"sequence": sequence, "action": action, "message": message}

    @staticmethod
    def _plan(
        operation: str,
        status: str,
        current: PluginInstallation,
        target: PluginInstallation | None,
        preconditions: list[dict],
        steps,
        compensation_steps,
        *,
        rollback_ready: bool,
        rollback_reason: str,
        next_action: str,
    ) -> dict:
        return {
            "schemaVersion": "1.0.0",
            "operation": operation,
            "status": status,
            "current": current.as_dict(),
            "target": target.as_dict() if target is not None else None,
            "preconditions": list(preconditions),
            "steps": list(steps),
            "compensationSteps": list(compensation_steps),
            "rollback": {
                "readyBeforeMutation": rollback_ready,
                "reason": rollback_reason,
            },
            "nextAction": next_action,
        }
