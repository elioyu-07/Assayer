"""MCP facade for the plugin installation lifecycle."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from assayer_platform.plugin_installation import PluginInstallationStore
from assayer_platform.plugin_lifecycle import PluginLifecycleManager
from assayer_platform.conformance import inspect_plugin_package
from assayer_host.plugin_lifecycle_mcp import PluginLifecycleMcpToolTransport
from assayer_host.plugin_lifecycle_ops import (
    CATALOG_READ_TIMEOUT_SECONDS,
    DEFAULT_CATALOG_URL,
    MUTATING_CATALOG_TIMEOUT_SECONDS,
    latest_available_from,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tests" / "fixtures" / "plugins" / "minimal"
POLICY_PACKAGE = ROOT / "tests" / "fixtures" / "plugins" / "policy-pack"
PLUGIN_ID = "test-minimal"


def _catalog_plan(**overrides):
    plan = {
        "operation": "install",
        "pluginId": "ass-spec",
        "currentVersion": None,
        "targetVersion": "0.9.0",
        "currentState": "absent",
        "nextState": "installed",
        "status": "ready",
        "preconditions": [],
        "source": "https://example.com/ass-spec-0.9.0.whl",
        "checksum": "abc123",
        "gates": [],
        "requiresConfirmation": True,
    }
    plan.update(overrides)
    return plan


def _catalog_text(*, plugin_id="ass-spec", version="0.9.0", sha256="a" * 64,
                  wheel="https://example.com/ass-spec-0.9.0.whl"):
    return json.dumps({
        "schemaVersion": "1.0.0",
        "plugins": {
            plugin_id: {
                "pluginId": plugin_id,
                "name": plugin_id,
                "description": "test catalog plugin",
                "versions": {
                    version: {
                        "pluginId": plugin_id,
                        "version": version,
                        "platformApiVersion": "1.0.0",
                        "wheelUrl": wheel,
                        "sha256": sha256,
                    },
                },
            },
        },
    })


def _fixture_wheel_bytes(*, exclude: frozenset[str] = frozenset()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PACKAGE.rglob("*")):
            relative = path.relative_to(PACKAGE).as_posix()
            if (
                not path.is_file()
                or "__pycache__" in path.parts
                or any(part.endswith(".egg-info") for part in path.parts)
                or path.suffix == ".pyc"
                or relative in exclude
            ):
                continue
            archive.writestr(relative, path.read_bytes())
    return buffer.getvalue()


class PluginLifecycleMcpTest(unittest.TestCase):
    def test_tool_names(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(directory)
            self.assertEqual(
                [item["name"] for item in transport.list_tools()],
                [
                    "verify_plugin_source", "list_plugins", "get_plugin_info", "plan_plugin_change",
                    "execute_plugin_change", "apply_plugin_change",
                ],
            )

    def test_tool_annotations_match_real_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(directory)
            tools = {item["name"]: item for item in transport.list_tools()}

        for name in ("list_plugins", "get_plugin_info", "plan_plugin_change"):
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"])
            self.assertFalse(tools[name]["annotations"]["destructiveHint"])
        for name in ("execute_plugin_change", "apply_plugin_change"):
            self.assertFalse(tools[name]["annotations"]["readOnlyHint"])
            self.assertTrue(tools[name]["annotations"]["destructiveHint"])
            self.assertFalse(tools[name]["annotations"]["idempotentHint"])
        verification = tools["verify_plugin_source"]["annotations"]
        self.assertFalse(verification["readOnlyHint"])
        self.assertFalse(verification["destructiveHint"])
        self.assertTrue(verification["idempotentHint"])
        for item in tools.values():
            self.assertTrue(item["annotations"]["openWorldHint"])

    def test_verify_plugin_source_builds_artifact_without_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "policy"
            shutil.copytree(POLICY_PACKAGE, source)
            store = root / "store"
            transport = PluginLifecycleMcpToolTransport(str(store))

            result = transport.call_tool(
                "verify_plugin_source", {"source": str(source)},
            )

            payload = result["structuredContent"]["result"]
            self.assertFalse(result["isError"], payload)
            self.assertEqual(payload["operation"], "verify_plugin_source")
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["pluginId"], "test.policy-pack")
            self.assertTrue(Path(payload["wheel"]).is_file())
            self.assertEqual(
                Path(payload["wheel"]).parent,
                (source / ".assayer" / "verified").resolve(),
            )
            self.assertRegex(payload["sha256"], r"^[a-f0-9]{64}$")
            self.assertFalse((store / "index.json").exists())

    def test_apply_by_name_install_runs_real_catalog_checksum_conformance_and_store_path(self):
        wheel = _fixture_wheel_bytes()
        digest = hashlib.sha256(wheel).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            catalog = root / "plugins.json"
            catalog.write_text(_catalog_text(
                plugin_id=PLUGIN_ID,
                version="1.0.0",
                sha256=digest,
                wheel="https://example.test/test-minimal-1.0.0.whl",
            ), encoding="utf-8")
            transport = PluginLifecycleMcpToolTransport(
                str(store), catalog_index=str(catalog),
            )

            with patch(
                "assayer_host.plugin_lifecycle_ops.download_bytes",
                return_value=wheel,
            ) as download:
                result = transport.call_tool(
                    "apply_plugin_change",
                    {"operation": "install", "plugin": PLUGIN_ID},
                )

            payload = result["structuredContent"]["result"]
            self.assertFalse(result["isError"], payload)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["pluginId"], PLUGIN_ID)
            self.assertEqual(payload["resultingState"], "installed")
            self.assertEqual(payload["activeVersion"], "1.0.0")
            download.assert_called_once_with("https://example.test/test-minimal-1.0.0.whl")
            installed = PluginLifecycleManager(PluginInstallationStore(store)).get(PLUGIN_ID)
            self.assertEqual(installed["state"], "installed")
            self.assertEqual(installed["activeVersion"], "1.0.0")

    def test_read_only_catalog_lookup_has_one_three_second_attempt(self):
        with patch(
            "assayer_host.plugin_lifecycle_ops.urllib.request.urlopen",
            side_effect=TimeoutError("catalog timeout"),
        ) as request:
            self.assertIsNone(latest_available_from("https://catalog.example/plugins.json"))

        request.assert_called_once_with(
            "https://catalog.example/plugins.json",
            timeout=CATALOG_READ_TIMEOUT_SECONDS,
        )
        self.assertEqual(CATALOG_READ_TIMEOUT_SECONDS, 3)

    def test_mutating_catalog_plan_has_a_longer_bounded_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(directory)
            with patch(
                "assayer_host.plugin_lifecycle_ops.urllib.request.urlopen",
                side_effect=TimeoutError("catalog timeout"),
            ) as request:
                result = transport.call_tool(
                    "plan_plugin_change", {"operation": "install", "plugin": "ass-spec"},
                )

        self.assertFalse(result["isError"])
        self.assertEqual(
            result["structuredContent"]["result"]["plan"]["blocker"]["code"],
            "PLUGIN_DOWNLOAD_FAILED",
        )
        self.assertEqual(request.call_count, 2)
        self.assertIsInstance(request.call_args_list[0].args[0], Request)
        self.assertEqual(
            request.call_args_list[0].args[0].full_url,
            "https://api.github.com/repos/elioyu-07/assayer-registry/"
            "contents/plugins.json?ref=main",
        )
        self.assertEqual(
            request.call_args_list[0].kwargs,
            {"timeout": MUTATING_CATALOG_TIMEOUT_SECONDS},
        )
        self.assertEqual(
            request.call_args_list[1].args,
            ("https://raw.githubusercontent.com/elioyu-07/assayer-registry/main/plugins.json",),
        )
        self.assertEqual(
            request.call_args_list[1].kwargs,
            {"timeout": MUTATING_CATALOG_TIMEOUT_SECONDS},
        )
        self.assertEqual(MUTATING_CATALOG_TIMEOUT_SECONDS, 10)

    def test_github_release_download_falls_back_to_asset_api(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.payload

        release_url = (
            "https://github.com/acme/spec/releases/download/v1.2.3/"
            "ass_spec-1.2.3-py3-none-any.whl"
        )
        metadata = json.dumps({"assets": [{
            "name": "ass_spec-1.2.3-py3-none-any.whl",
            "url": "https://api.github.com/repos/acme/spec/releases/assets/123",
        }]}).encode("utf-8")
        wheel = b"wheel-bytes"
        with patch(
            "assayer_host.plugin_lifecycle_ops.urllib.request.urlopen",
            side_effect=[Response(metadata), Response(wheel)],
        ) as request:
            from assayer_host.plugin_lifecycle_ops import download_bytes

            self.assertEqual(download_bytes(release_url, timeout_seconds=7), wheel)

        calls = request.call_args_list
        metadata_request = calls[0].args[0]
        asset_request = calls[1].args[0]
        self.assertIsInstance(metadata_request, Request)
        self.assertEqual(
            metadata_request.full_url,
            "https://api.github.com/repos/acme/spec/releases/tags/v1.2.3",
        )
        self.assertIsInstance(asset_request, Request)
        self.assertEqual(
            asset_request.full_url,
            "https://api.github.com/repos/acme/spec/releases/assets/123",
        )
        self.assertEqual(asset_request.get_header("Accept"), "application/octet-stream")

    def test_github_asset_api_failure_falls_back_to_public_release_url(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"wheel-bytes"

        release_url = (
            "https://github.com/acme/spec/releases/download/v1.2.3/"
            "ass_spec-1.2.3-py3-none-any.whl"
        )
        with patch(
            "assayer_host.plugin_lifecycle_ops.urllib.request.urlopen",
            side_effect=[TimeoutError("api unavailable"), Response()],
        ) as request:
            from assayer_host.plugin_lifecycle_ops import download_bytes

            self.assertEqual(download_bytes(release_url, timeout_seconds=7), b"wheel-bytes")

        self.assertIsInstance(request.call_args_list[0].args[0], Request)
        self.assertEqual(request.call_args_list[1].args, (release_url,))
        self.assertEqual(request.call_args_list[1].kwargs, {"timeout": 7})

    def test_github_raw_catalog_uses_contents_api(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"schemaVersion":"1.0.0"}'

        raw_url = (
            "https://raw.githubusercontent.com/elioyu-07/assayer-registry/"
            "main/plugins.json"
        )
        with patch(
            "assayer_host.plugin_lifecycle_ops.urllib.request.urlopen",
            return_value=Response(),
        ) as request:
            from assayer_host.plugin_lifecycle_ops import download_bytes

            self.assertEqual(
                download_bytes(raw_url, timeout_seconds=7),
                b'{"schemaVersion":"1.0.0"}',
            )

        api_request = request.call_args.args[0]
        self.assertIsInstance(api_request, Request)
        self.assertEqual(
            api_request.full_url,
            "https://api.github.com/repos/elioyu-07/assayer-registry/"
            "contents/plugins.json?ref=main",
        )
        self.assertEqual(
            api_request.get_header("Accept"), "application/vnd.github.raw+json",
        )

    def test_local_repository_is_installed_only_as_its_verified_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "plugin-source"
            shutil.copytree(PACKAGE, package)
            for relative in (
                ".git/config",
                ".venv/bin/python",
                "build/output.txt",
                "dist/old.whl",
                "tests/test_local_only.py",
            ):
                path = package / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("must not be installed", encoding="utf-8")
            store = root / "store"
            transport = PluginLifecycleMcpToolTransport(str(store))

            planned = transport.call_tool(
                "plan_plugin_change", {"operation": "install", "plugin": str(package)},
            )
            installed = transport.call_tool("execute_plugin_change", {
                "token": planned["structuredContent"]["result"]["token"],
                "confirmed": True,
            })

            payload = installed["structuredContent"]["result"]
            self.assertFalse(installed["isError"], payload)
            self.assertRegex(payload["wheelSha256"], r"^[a-f0-9]{64}$")
            installed_root = store / "packages" / PLUGIN_ID / "1.0.0"
            forbidden = {".git", ".venv", "tests", "build", "dist", "__pycache__"}
            self.assertFalse(any(forbidden.intersection(path.parts) for path in installed_root.rglob("*")))
            record = PluginLifecycleManager(
                PluginInstallationStore(store),
            ).get(PLUGIN_ID)["versions"]["1.0.0"]
            self.assertEqual(record["wheelSha256"], payload["wheelSha256"])

    def test_policy_pack_local_source_compiles_before_wheel_only_install(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "store"
            transport = PluginLifecycleMcpToolTransport(str(store))
            planned = transport.call_tool(
                "plan_plugin_change",
                {"operation": "install", "plugin": str(POLICY_PACKAGE)},
            )
            plan = planned["structuredContent"]["result"]["plan"]
            self.assertEqual(plan["pluginId"], "test.policy-pack")
            self.assertEqual(plan["targetVersion"], "1.0.0")

            installed = transport.call_tool("execute_plugin_change", {
                "token": planned["structuredContent"]["result"]["token"],
                "confirmed": True,
            })
            payload = installed["structuredContent"]["result"]
            self.assertFalse(installed["isError"], payload)
            self.assertEqual(payload["pluginId"], "test.policy-pack")
            installed_root = store / "packages" / "test.policy-pack" / "1.0.0"
            self.assertTrue((installed_root / "assayer-plugin-release.json").is_file())
            self.assertFalse((installed_root / "plugin.py").exists())
            self.assertFalse((installed_root / "plugin.yaml").exists())

    def test_info_reports_catalog_version_relations_without_reinstalling(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(
                str(Path(directory) / "store"),
            )
            planned = transport.call_tool(
                "plan_plugin_change", {"operation": "install", "plugin": str(PACKAGE)},
            )
            transport.call_tool("execute_plugin_change", {
                "token": planned["structuredContent"]["result"]["token"],
                "confirmed": True,
            })

            cases = (
                (lambda _plugin_id: "1.1.0", "available", "1.1.0", "update_available", True),
                (None, "unavailable", None, "unknown", False),
                (lambda _plugin_id: "0.9.0", "available", "0.9.0", "installed_ahead_of_catalog", True),
            )
            for catalog, status, latest, relation, known in cases:
                with self.subTest(relation=relation), patch(
                    "assayer_host.plugin_lifecycle_mcp.latest_available_from",
                    return_value=catalog,
                ):
                    info = transport.call_tool(
                        "get_plugin_info", {"pluginId": PLUGIN_ID},
                    )["structuredContent"]["result"]
                self.assertEqual(info["activeVersion"], "1.0.0")
                self.assertEqual(info["catalogStatus"], status)
                self.assertEqual(info["latestAvailableVersion"], latest)
                self.assertEqual(info["latestVersionKnown"], known)
                self.assertEqual(info["versionRelation"], relation)

    def test_catalog_change_invalidates_plan_token(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(_catalog_text(), encoding="utf-8")
            transport = PluginLifecycleMcpToolTransport(
                str(Path(directory) / "store"), catalog_index=str(catalog),
            )
            planned = transport.call_tool(
                "plan_plugin_change", {"operation": "install", "plugin": "ass-spec", "version": "0.9.0"},
            )
            self.assertFalse(planned["isError"])
            self.assertEqual(planned["structuredContent"]["result"]["plan"]["status"], "ready")
            token = planned["structuredContent"]["result"]["token"]

            catalog.write_text(_catalog_text(sha256="b" * 64), encoding="utf-8")

            result = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["result"]["error"]["code"], "PLAN_STALE")

    def test_lifecycle_transport_rejects_invalid_requests_structurally(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(directory)
            for name, arguments in (("verify_plugin_source", {}), ("get_plugin_info", {}), ("plan_plugin_change", {}),
                                    ("execute_plugin_change", {"confirmed": True}),
                                    ("plan_plugin_change", {"operation": "install", "version": "1"}),
                                    ("plan_plugin_change", {"operation": "unknown", "plugin": "ass-spec"})):
                result = transport.call_tool(name, arguments)
                self.assertTrue(result["isError"], name)
                error = result["structuredContent"]["result"]["error"]
                self.assertEqual(error["code"], "INVALID_REQUEST", name)
                self.assertFalse(error["retryable"], name)
                self.assertEqual(error["nextAction"], "correct_request", name)

    def test_unknown_tool_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = PluginLifecycleMcpToolTransport(directory)
            with self.assertRaises(Exception):
                transport.call_tool("nope", {})

    def test_plan_then_execute_install_is_one_shot(self):
        with tempfile.TemporaryDirectory() as directory:
            store_root = str(Path(directory) / "store")
            transport = PluginLifecycleMcpToolTransport(store_root)
            with patch(
                "assayer_host.plugin_lifecycle_mcp.plan_plugin_change",
                return_value=_catalog_plan(),
            ), patch(
                "assayer_host.plugin_lifecycle_ops.add_from_catalog",
                return_value={"operation": "install", "status": "completed",
                              "pluginId": "ass-spec", "version": "0.9.0"},
            ) as add:
                planned = transport.call_tool("plan_plugin_change", {"operation": "install", "plugin": "ass-spec", "version": "0.9.0"})
                self.assertFalse(planned["isError"])
                token = planned["structuredContent"]["result"]["token"]

                executed = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
                self.assertFalse(executed["isError"])
                self.assertEqual(executed["structuredContent"]["result"]["status"], "completed")
                self.assertEqual(executed["structuredContent"]["result"]["resultingState"], "installed")

                replayed = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
                self.assertTrue(replayed["isError"])
                self.assertEqual(
                    replayed["structuredContent"]["result"]["error"]["code"], "PLAN_TOKEN_USED",
                )
            add.assert_called_once_with(
                "ass-spec", version="0.9.0",
                index=DEFAULT_CATALOG_URL, store_root=store_root,
            )

    def test_plan_and_release_gate_share_the_complete_descriptor_validator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            shutil.copytree(PACKAGE, package)
            descriptor_path = package / "assayer-plugin-release.json"
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            descriptor["undeclaredInstallerField"] = True
            descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")

            release_report = inspect_plugin_package(package)
            self.assertFalse(release_report.passed)
            release_code = release_report.issues[0].code

            store_root = root / "store"
            transport = PluginLifecycleMcpToolTransport(str(store_root))
            planned = transport.call_tool("plan_plugin_change", {
                "operation": "install", "plugin": str(package),
            })
            self.assertTrue(planned["isError"])
            error = planned["structuredContent"]["result"]["error"]
            self.assertEqual(error["code"], release_code)
            self.assertEqual(error["code"], "PLUGIN_RELEASE_DESCRIPTOR_INVALID")
            self.assertFalse((store_root / "index.json").exists())

    def test_local_source_change_after_plan_invalidates_token_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            shutil.copytree(PACKAGE, package)
            transport = PluginLifecycleMcpToolTransport(str(root / "store"))
            planned = transport.call_tool("plan_plugin_change", {
                "operation": "install", "plugin": str(package),
            })
            token = planned["structuredContent"]["result"]["token"]
            semantic = package / "src" / "minimal_plugin" / "semantic-review.md"
            semantic.write_text(semantic.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")

            result = transport.call_tool(
                "execute_plugin_change", {"token": token, "confirmed": True},
            )

            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"],
                "PLAN_STALE",
            )
            self.assertFalse((root / "store" / "index.json").exists())

    def test_execute_catalog_unavailable_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(_catalog_text(), encoding="utf-8")
            transport = PluginLifecycleMcpToolTransport(
                str(Path(directory) / "store"), catalog_index=str(catalog),
            )
            planned = transport.call_tool(
                "plan_plugin_change", {"operation": "install", "plugin": "ass-spec", "version": "0.9.0"},
            )
            self.assertFalse(planned["isError"])
            token = planned["structuredContent"]["result"]["token"]

            catalog.unlink()

            result = transport.call_tool("execute_plugin_change", {"token": token, "confirmed": True})
            self.assertTrue(result["isError"])
            self.assertEqual(
                result["structuredContent"]["result"]["error"]["code"], "PLUGIN_CATALOG_UNAVAILABLE",
            )


if __name__ == "__main__":
    unittest.main()
