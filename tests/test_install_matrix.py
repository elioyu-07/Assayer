"""Unit coverage for the phase-2 install-matrix assertion logic.

The matrix itself runs in its own CI job because it builds wheels and creates
clean virtual environments.  These tests exercise the pure assertion helper so
a broken guard fails fast in the default suite.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_matrix_module():
    name = "assayer_install_matrix"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "install_matrix.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


matrix = _load_matrix_module()


class InstallMatrixAssertionsTest(unittest.TestCase):
    def test_matrix_covers_split_runtime_shapes_without_python_plugins(self):
        names = {case.name for case in matrix.CASES}
        self.assertIn("all-split", names)
        split = next(case for case in matrix.CASES if case.name == "all-split")
        self.assertEqual((0, 0), (split.ordinary_plugins, split.providers))
        self.assertTrue(split.agent)
        self.assertIn("agent-only", names)
        agent_only = next(case for case in matrix.CASES if case.name == "agent-only")
        self.assertFalse(agent_only.platform)
        root = next(case for case in matrix.CASES if case.name == "root-meta")
        self.assertEqual((0, 0), (root.ordinary_plugins, root.providers))

    def test_root_wheel_guard_rejects_module_and_sdk_schema_leaks(self):
        import tempfile
        import zipfile

        leaks = {
            "module": {"assayer_plugin_sdk/__init__.py": ""},
            "schema": {
                "assayer_host/__init__.py": "",
                "assayer-0.1.2.data/data/share/assayer/schemas/common.schema.json": "{}",
            },
        }
        for name, members in leaks.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "assayer-0.1.2-py3-none-any.whl"
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("assayer_platform/__init__.py", "")
                    for member, content in members.items():
                        archive.writestr(member, content)
                with self.assertRaises(SystemExit):
                    matrix.assert_root_wheel_is_platform_only(Path(directory))

    def test_clean_staging_removes_stale_build_outputs(self):
        import tempfile

        import build_distributions as build_dists

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "assayer.egg-info").mkdir(parents=True)
            (root / "build" / "lib" / "assayer_plugin_sdk").mkdir(parents=True)
            (root / "packages" / "x" / "build").mkdir(parents=True)
            build_dists.clean_staging(root)
            self.assertFalse((root / "build").exists())
            self.assertFalse((root / "src" / "assayer.egg-info").exists())
            self.assertFalse((root / "packages" / "x" / "build").exists())

    def test_built_wheels_guard_matches_requested_names_not_just_count(self):
        import tempfile

        import build_distributions as build_dists

        requested = build_dists.DISTRIBUTIONS
        clean = [f"{name.replace('-', '_')}-0.1.2-py3-none-any.whl" for name in requested]
        retired = "assayer_plugin_frontend_audit-0.1.0-py3-none-any.whl"

        with tempfile.TemporaryDirectory() as directory:
            path_root = Path(directory)

            def write_wheels(filenames):
                for existing in path_root.glob("*.whl"):
                    existing.unlink()
                for filename in filenames:
                    (path_root / filename).write_text("", encoding="utf-8")

            cases = {
                "missing": clean[:-1],
                "duplicate": clean + [clean[0].replace("0.1.2", "0.1.1")],
                "retired": clean + [retired],
            }
            for name, wheels in cases.items():
                with self.subTest(name=name):
                    write_wheels(wheels)
                    with self.assertRaises(SystemExit):
                        build_dists.assert_built_wheels(path_root)

            write_wheels(clean + ["assayer-0.1.2-py3-none-any.whl"])
            built = build_dists.assert_built_wheels(path_root)
            self.assertEqual([wheel.name for wheel in built], clean)

    def test_reset_wheelhouse_removes_stale_and_retired_artifacts(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            wheelhouse = Path(directory) / "wheelhouse"
            (wheelhouse / "nested").mkdir(parents=True)
            for name in (
                "assayer_plugin_frontend_audit-0.1.0-py3-none-any.whl",
                "assayer_provider_browser-0.1.0-py3-none-any.whl",
            ):
                (wheelhouse / name).write_text("", encoding="utf-8")
            matrix.reset_wheelhouse(wheelhouse)
            self.assertTrue(wheelhouse.is_dir())
            self.assertEqual([], sorted(wheelhouse.iterdir()))

if __name__ == "__main__":
    unittest.main()
