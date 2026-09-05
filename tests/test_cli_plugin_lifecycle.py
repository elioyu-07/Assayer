"""CLI verbs for the durable plugin lifecycle (install/upgrade/downgrade/rollback/uninstall/info)."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from assayer_host import cli

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "plugins" / "touchstone"
PLUGIN_ID = "assayer.touchstone"


def _run(argv: list[str], confirm=lambda plan: True) -> tuple[int, dict]:
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(argv, confirm=confirm)
    return code, json.loads(output.getvalue())


def _run_raw(argv: list[str], confirm=lambda plan: True) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(argv, confirm=confirm)
    return code, output.getvalue()


def _bumped_copy(version: str, directory: Path) -> Path:
    destination = directory / f"touchstone-{version}"
    shutil.copytree(PACKAGE, destination)
    descriptor = json.loads((destination / "assayer-plugin-release.json").read_text(encoding="utf-8"))
    descriptor["pluginVersion"] = version
    (destination / "assayer-plugin-release.json").write_text(json.dumps(descriptor), encoding="utf-8")
    manifest_path = destination / "src" / "assayer_touchstone" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    metadata = destination / "pyproject.toml"
    metadata.write_text(metadata.read_text(encoding="utf-8").replace(
        'version = "1.0.0"', f'version = "{version}"',
    ))
    return destination


class CliPluginLifecycleTest(unittest.TestCase):
    def test_install_info_uninstall_journey(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, result = _run(["plugins", "install", str(PACKAGE), "--store", str(store)])
            self.assertEqual(code, 0)
            self.assertEqual(result["operation"], "install")
            self.assertEqual(result["pluginId"], PLUGIN_ID)
            self.assertEqual(result["version"], "1.0.0")

            code, info = _run(["plugins", "info", PLUGIN_ID, "--store", str(store)])
            self.assertEqual(code, 0)
            self.assertEqual(info["activeVersion"], "1.0.0")
            self.assertEqual(info["history"], ["1.0.0"])

            code, removed = _run(["plugins", "uninstall", PLUGIN_ID, "--store", str(store)])
            self.assertEqual(code, 0)
            self.assertEqual(removed["status"], "completed")

            code, missing = _run(["plugins", "info", PLUGIN_ID, "--store", str(store)])
            self.assertEqual(code, 2)
            self.assertEqual(missing["error"]["code"], "UNKNOWN_PLUGIN")

    def test_install_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            self.assertEqual(_run(["plugins", "install", str(PACKAGE), "--store", str(store)])[0], 0)
            code, result = _run(["plugins", "install", str(PACKAGE), "--store", str(store)])
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "PLUGIN_CONFLICT")

    def test_upgrade_downgrade_rollback_journey(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            self.assertEqual(_run(["plugins", "install", str(PACKAGE), "--store", str(store)])[0], 0)

            code, upgraded = _run([
                "plugins", "upgrade", str(_bumped_copy("1.1.0", root)), "--store", str(store),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(upgraded["version"], "1.1.0")
            self.assertEqual(upgraded["previousVersion"], "1.0.0")

            code, downgraded = _run([
                "plugins", "downgrade", PLUGIN_ID, "1.0.0", "--store", str(store),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(downgraded["version"], "1.0.0")
            self.assertEqual(downgraded["previousVersion"], "1.1.0")

            code, rolled = _run(["plugins", "rollback", PLUGIN_ID, "--store", str(store)])
            self.assertEqual(code, 2)
            self.assertEqual(rolled["error"]["code"], "ROLLBACK_UNAVAILABLE")

    def test_upgrade_to_older_requires_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            self.assertEqual(_run(["plugins", "install", str(_bumped_copy("2.0.0", root)), "--store", str(store)])[0], 0)
            code, result = _run([
                "plugins", "upgrade", str(PACKAGE), "--store", str(store),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "PLUGIN_DOWNGRADE_REQUIRED")

    def test_downgrade_unavailable_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            self.assertEqual(_run(["plugins", "install", str(PACKAGE), "--store", str(store)])[0], 0)
            code, result = _run(["plugins", "downgrade", PLUGIN_ID, "9.9.9", "--store", str(store)])
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "PLUGIN_VERSION_UNAVAILABLE")

    def test_list_and_run_see_installed_store_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            self.assertEqual(_run(["plugins", "install", str(PACKAGE), "--store", str(store)])[0], 0)

            code, listing = _run(["plugins", "list", "--json", "--store", str(store)])
            self.assertEqual(code, 0)
            spec = next(
                item for item in listing["plugins"] if item["pluginId"] == PLUGIN_ID
            )
            self.assertEqual(spec["checks"], [{"checkId": "SPEC-001", "version": "1.0.0"}])
            self.assertEqual(spec["executionModes"], ["interactive"])

            code, result = _run([
                "plugins", "run", "--plugin", PLUGIN_ID, "--check", "SPEC-001",
                "--scope-json", "{}", "--store", str(store),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "PLUGIN_EXECUTION_MODE_UNSUPPORTED")

    def _bad_package(self, directory: Path, plugin_id: str = "assayer.bad", version: str = "1.0.0") -> Path:
        root = directory / f"bad-{plugin_id}"
        root.mkdir()
        (root / "assayer-plugin-release.json").write_text(json.dumps({
            "pluginId": plugin_id,
            "pluginVersion": version,
        }), encoding="utf-8")
        return root

    def test_bad_install_quarantines_and_refuses_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, installed = _run([
                "plugins", "install", str(self._bad_package(root)), "--store", str(store),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(installed["status"], "quarantined")
            self.assertEqual(installed["state"], "dirty")
            self.assertEqual(installed["reason"], "PLUGIN_RELEASE_DESCRIPTOR_INVALID")

            code, listing = _run(["plugins", "list", "--json", "--store", str(store)])
            self.assertEqual(code, 0)
            self.assertEqual([item["pluginId"] for item in listing["plugins"]],
                             ["assayer.config-quality", "assayer.frontend-audit"])
            self.assertEqual(
                [item["pluginId"] for item in listing["quarantined"]], ["assayer.bad"],
            )
            self.assertEqual(listing["quarantined"][0]["stateReason"], "PLUGIN_RELEASE_DESCRIPTOR_INVALID")

            code, info = _run(["plugins", "info", "assayer.bad", "--store", str(store)])
            self.assertEqual(code, 0)
            self.assertEqual(info["state"], "dirty")
            self.assertEqual(info["stateReason"], "PLUGIN_RELEASE_DESCRIPTOR_INVALID")

            code, result = _run([
                "plugins", "run", "--plugin", "assayer.bad", "--check", "X",
                "--scope-json", "{}", "--store", str(store),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "PLUGIN_DIRTY")

    def test_install_unreachable_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, result = _run([
                "plugins", "install", str(root / "missing"), "--store", str(store),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "PLUGIN_SOURCE_UNREACHABLE")

    def test_list_without_store_reports_builtins_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, listing = _run(["plugins", "list", "--json", "--store", str(store)])
            self.assertEqual(code, 0)
            self.assertEqual(
                [item["pluginId"] for item in listing["plugins"]],
                ["assayer.config-quality", "assayer.frontend-audit"],
            )

    def test_install_requires_confirmation_when_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, output = _run_raw(
                ["plugins", "install", str(PACKAGE), "--store", str(store)],
                confirm=lambda plan: False,
            )
            self.assertEqual(code, 0)
            self.assertIn('"aborted"', output)
            self.assertIn('"confirmation required"', output)
            self.assertFalse((store / "index.json").exists())

    def test_install_with_yes_skips_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, result = _run([
                "plugins", "install", str(PACKAGE), "--store", str(store), "--yes",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(result["pluginId"], PLUGIN_ID)
            self.assertEqual(result["version"], "1.0.0")

    def test_nl_install_list_info_journey(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, result = _run(["install", str(PACKAGE), "--store", str(store), "--yes"])
            self.assertEqual(code, 0)
            self.assertEqual(result["plan"], [{"operation": "install", "package": str(PACKAGE.resolve())}])
            self.assertEqual(result["results"][0]["pluginId"], PLUGIN_ID)

            code, listing = _run(["what", "plugins", "do", "i", "have", "--store", str(store)])
            self.assertEqual(code, 0)
            self.assertIn(
                PLUGIN_ID,
                [item["pluginId"] for item in listing["results"][0]["plugins"]],
            )

            code, info = _run(["tell", "me", "about", "touchstone", "--store", str(store)])
            self.assertEqual(code, 0)
            self.assertEqual(info["results"][0]["activeVersion"], "1.0.0")

    def test_nl_install_bare_name_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, result = _run(["install", "touchstone", "--store", str(store), "--yes"])
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "INTENT_PACKAGE_UNRESOLVED")

    def test_nl_run_without_check_asks_for_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, result = _run(["review", "spec.md", "with", "touchstone", "--store", str(store)])
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "INTENT_NEEDS_DETAIL")

    def test_run_accepts_scope_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "settings.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            scope_file = root / "scope.json"
            scope_file.write_text(json.dumps({
                "files": [{
                    "path": str(source),
                    "requiredKeys": ["enabled"],
                    "expectedTypes": {"enabled": "boolean"},
                }]
            }), encoding="utf-8")
            code, result = _run([
                "plugins", "run", "--plugin", "assayer.config-quality",
                "--check", "CFG-001", "--scope", str(scope_file),
                "--output-root", str(root / "output"),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["decisions"], ["scanned_no_issue"])


if __name__ == "__main__":
    unittest.main()
