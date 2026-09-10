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


def observed(*, plugins=(), providers=(), origins=None, plugin_count=None,
             conflict=None, purelib="/venv/site-packages"):
    return {
        "plugins": list(plugins),
        "providers": list(providers),
        "origins": origins or {},
        "purelib": purelib,
        "plugin_count": plugin_count,
        "conflict": conflict,
    }


class InstallMatrixAssertionsTest(unittest.TestCase):
    def test_matrix_covers_the_split_shapes_and_the_conflict(self):
        names = {case.name for case in matrix.CASES}
        self.assertIn("all-split", names)
        self.assertIn("root+split-conflict", names)
        conflict = next(case for case in matrix.CASES if case.name == "root+split-conflict")
        self.assertEqual("PLUGIN_CONFLICT", conflict.conflict)
        split = next(case for case in matrix.CASES if case.name == "all-split")
        self.assertEqual((1, 1), (split.plugins, split.providers))

    def test_valid_observation_passes(self):
        case = matrix.Case("ok", ("assayer",), 1, 1)
        matrix._assert_case(case, observed(
            plugins=["assayer.frontend-audit"],
            providers=["markdown"],
            origins={
                "assayer.frontend-audit": "/venv/site-packages/assayer_frontend_audit/__init__.py",
                "markdown": "/venv/site-packages/assayer_document_navigation/__init__.py",
            },
            plugin_count=1,
        ))

    def test_plugin_count_mismatch_fails(self):
        case = matrix.Case("p", ("assayer",), 1, 0)
        with self.assertRaises(SystemExit):
            matrix._assert_case(case, observed(plugins=[], plugin_count=0))

    def test_provider_count_mismatch_fails(self):
        case = matrix.Case("q", ("assayer",), 0, 1)
        with self.assertRaises(SystemExit):
            matrix._assert_case(case, observed(providers=[], plugin_count=0))

    def test_unexpected_conflict_fails(self):
        case = matrix.Case("r", ("assayer",), 1, 0)
        with self.assertRaises(SystemExit):
            matrix._assert_case(case, observed(
                plugins=["assayer.frontend-audit"], plugin_count=None, conflict="PLUGIN_CONFLICT",
            ))

    def test_missing_expected_conflict_fails(self):
        case = matrix.Case("s", ("assayer",), 2, 1, conflict="PLUGIN_CONFLICT")
        with self.assertRaises(SystemExit):
            matrix._assert_case(case, observed(
                plugins=["assayer.frontend-audit", "assayer.frontend-audit"],
                providers=["markdown"], plugin_count=1, conflict=None,
            ))

    def test_registry_did_not_load_expected_plugins_fails(self):
        case = matrix.Case("t", ("assayer",), 1, 0)
        with self.assertRaises(SystemExit):
            matrix._assert_case(case, observed(
                plugins=["assayer.frontend-audit"], plugin_count=0,
            ))

    def test_entry_point_outside_site_packages_fails(self):
        case = matrix.Case("u", ("assayer",), 1, 0)
        with self.assertRaises(SystemExit):
            matrix._assert_case(case, observed(
                plugins=["assayer.frontend-audit"],
                origins={"assayer.frontend-audit": "/somewhere/else/__init__.py"},
                plugin_count=1,
            ))

    def test_unresolved_entry_point_fails(self):
        case = matrix.Case("v", ("assayer",), 1, 0)
        with self.assertRaises(SystemExit):
            matrix._assert_case(case, observed(
                plugins=["assayer.frontend-audit"],
                origins={"assayer.frontend-audit": None},
                plugin_count=1,
            ))


if __name__ == "__main__":
    unittest.main()
