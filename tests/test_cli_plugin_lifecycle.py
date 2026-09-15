"""CLI verbs for the durable plugin lifecycle (install/upgrade/downgrade/rollback/uninstall/info)."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from assayer_host import cli
from assayer_host.plugin_lifecycle_ops import load_scope
from assayer_platform import PlatformContractError, PluginRegistry
from tests.helpers import config_quality_registration
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tests" / "fixtures" / "plugins" / "minimal"
PLUGIN_ID = "test-minimal"


def _run(argv: list[str], confirm=lambda plan: True) -> tuple[int, dict]:
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(argv, confirm=confirm)
    return code, json.loads(output.getvalue())


class CliPluginLifecycleTest(unittest.TestCase):
    def test_load_scope_rejects_missing_or_non_json_scope(self):
        for value in (None, 123, "not-json"):
            with self.subTest(value=value):
                with self.assertRaises(PlatformContractError) as caught:
                    load_scope(value, None)
                self.assertEqual(caught.exception.code, "INVALID_SCOPE")

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
            self.assertEqual(spec["checks"], [{"checkId": "TST-001", "version": "1.0.0"}])
            self.assertEqual(spec["executionModes"], ["interactive"])

            code, result = _run([
                "plugins", "run", "--plugin", PLUGIN_ID, "--check", "TST-001",
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
                             ["assayer.frontend-audit"])
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

            code, info = _run(["tell", "me", "about", "test-minimal", "--store", str(store)])
            self.assertEqual(code, 0)
            self.assertEqual(info["results"][0]["activeVersion"], "1.0.0")

    def test_nl_install_by_name_routes_to_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            with patch("assayer_host.plugin_lifecycle_ops.add_from_catalog", return_value={
                "operation": "install", "status": "completed",
                "pluginId": PLUGIN_ID, "version": "1.0.0",
            }) as add:
                code, result = _run(["install", "test-minimal", "--store", str(store), "--yes"])
            self.assertEqual(code, 0)
            self.assertEqual(result["plan"], [{"operation": "install", "pluginId": "test-minimal"}])
            self.assertEqual(result["results"][0]["status"], "completed")
            add.assert_called_once_with(
                "test-minimal", version=None, index=cli.DEFAULT_CATALOG_URL, store_root=str(store),
            )

    def test_nl_run_without_check_asks_for_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            code, result = _run(["review", "spec.md", "with", "test-minimal", "--store", str(store)])
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
            registry = PluginRegistry((config_quality_registration(),))
            with patch("assayer_host.plugin_lifecycle_ops.installed_plugin_registry", return_value=registry):
                code, result = _run([
                    "plugins", "run", "--plugin", "test.config-quality",
                    "--check", "CFG-001", "--scope", str(scope_file),
                    "--output-root", str(root / "output"),
                ])
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["decisions"], ["scanned_no_issue"])


if __name__ == "__main__":
    unittest.main()
