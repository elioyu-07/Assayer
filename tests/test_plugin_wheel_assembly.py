from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from assayer_platform.plugin_packaging import (
    assemble_self_contained_wheel,
    sha256_hex,
    wheel_descriptor,
)


def _wheel(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class WheelDescriptorTests(unittest.TestCase):
    def test_strips_runtime_source_prefix(self):
        descriptor = {
            "manifest": "src/test_minimal/manifest.json",
            "scopeSchema": "src/test_minimal/scope.schema.json",
            "runtimeSource": "src",
        }
        result = wheel_descriptor(descriptor)
        self.assertEqual(result["manifest"], "test_minimal/manifest.json")
        self.assertEqual(result["scopeSchema"], "test_minimal/scope.schema.json")
        self.assertEqual(result["runtimeSource"], ".")

    def test_leaves_root_relative_paths_alone(self):
        descriptor = {
            "runtimeSource": ".",
            "manifest": "test_minimal/manifest.json",
            "scopeSchema": "test_minimal/scope.schema.json",
        }
        result = wheel_descriptor(descriptor)
        self.assertEqual(result["manifest"], "test_minimal/manifest.json")
        self.assertEqual(result["runtimeSource"], ".")


class AssembleWheelTests(unittest.TestCase):
    def _source(self, tmp: Path) -> Path:
        source = tmp / "plugin"
        source.mkdir()
        (source / "pyproject.toml").write_text("[project]\nname = \"test-minimal\"\n", encoding="utf-8")
        (source / "semantic-review.md").write_text("# review\n", encoding="utf-8")
        (source / "fixtures").mkdir()
        (source / "fixtures" / "smoke.json").write_text("{}", encoding="utf-8")
        return source

    def _descriptor(self):
        return {
            "schemaVersion": "1.0.0",
            "pluginId": "test-minimal",
            "pluginVersion": "1.0.0",
            "platformApiVersion": "1.0.0",
            "registration": "test_minimal:registration",
            "manifest": "src/test_minimal/manifest.json",
            "scopeSchema": "src/test_minimal/scope.schema.json",
            "runtimeSource": "src",
            "semanticReview": "semantic-review.md",
            "fixtures": ["fixtures/smoke.json"],
            "packageMetadata": "pyproject.toml",
            "conformance": {"contractVersion": "1.0.0"},
        }

    def test_injects_descriptor_and_resources(self):
        built = _wheel({
            "test_minimal/__init__.py": b"",
            "test_minimal/manifest.json": b"{}",
            "test_minimal/scope.schema.json": b"{}",
            "test_minimal-1.0.0.dist-info/METADATA": b"Metadata-Version: 2.1\n",
            "test_minimal-1.0.0.dist-info/RECORD": b"",
        })
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source(Path(tmp))
            result = assemble_self_contained_wheel(built, source, self._descriptor())
            with zipfile.ZipFile(io.BytesIO(result)) as archive:
                names = set(archive.namelist())
                descriptor = json.loads(archive.read("assayer-plugin-release.json"))
                self.assertEqual(descriptor["runtimeSource"], ".")
                self.assertEqual(descriptor["manifest"], "test_minimal/manifest.json")
                self.assertIn("semantic-review.md", names)
                self.assertIn("fixtures/smoke.json", names)
                self.assertIn("pyproject.toml", names)
                self.assertIn("test_minimal-1.0.0.dist-info/RECORD", names)

    def test_record_hashes_match_files(self):
        built = _wheel({
            "test_minimal/__init__.py": b"code",
            "test_minimal-1.0.0.dist-info/METADATA": b"Metadata-Version: 2.1\n",
            "test_minimal-1.0.0.dist-info/RECORD": b"",
        })
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source(Path(tmp))
            result = assemble_self_contained_wheel(built, source, self._descriptor())
            with zipfile.ZipFile(io.BytesIO(result)) as archive:
                record = archive.read("test_minimal-1.0.0.dist-info/RECORD").decode()
            listed = {}
            for row in csv.reader(io.StringIO(record)):
                if not row:
                    continue
                listed[row[0]] = (row[1], row[2])
            with zipfile.ZipFile(io.BytesIO(result)) as archive:
                for name in archive.namelist():
                    if name == "test_minimal-1.0.0.dist-info/RECORD":
                        continue
                    self.assertIn(name, listed)
                    digest, size = listed[name]
                    content = archive.read(name)
                    expected = "sha256=" + base64.urlsafe_b64encode(
                        hashlib.sha256(content).digest()
                    ).rstrip(b"=").decode()
                    self.assertEqual(digest, expected, name)
                    self.assertEqual(size, str(len(content)), name)


if __name__ == "__main__":
    unittest.main()
