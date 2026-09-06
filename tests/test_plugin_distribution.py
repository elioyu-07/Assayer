from __future__ import annotations

import hashlib
import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from assayer_platform import PlatformContractError
from assayer_platform.plugin_distribution import (
    extract_archive,
    materialize_wheel,
    verify_sha256,
)


def _wheel(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _descriptor() -> dict:
    return {
        "schemaVersion": "1.0.0",
        "pluginId": "test-minimal",
        "pluginVersion": "1.0.0",
        "platformApiVersion": "1.0.0",
        "registration": "test_minimal:registration",
        "manifest": "test_minimal/manifest.json",
        "scopeSchema": "test_minimal/scope.schema.json",
        "runtimeSource": ".",
        "semanticReview": "semantic-review.md",
        "fixtures": ["fixtures/smoke.json"],
        "packageMetadata": "pyproject.toml",
        "conformance": {"contractVersion": "1.0.0"},
    }


def _self_contained_wheel() -> bytes:
    return _wheel({
        "assayer-plugin-release.json": json.dumps(_descriptor()).encode(),
        "test_minimal/__init__.py": b"",
        "test_minimal/manifest.json": b"{}",
        "test_minimal/scope.schema.json": b"{}",
        "semantic-review.md": b"# review",
        "fixtures/smoke.json": b"{}",
        "pyproject.toml": b"[project]\nname = \"test-minimal\"\n",
    })


class VerifySha256Tests(unittest.TestCase):
    def test_matching_digest_passes(self):
        data = b"hello"
        verify_sha256(data, hashlib.sha256(data).hexdigest())

    def test_mismatch_raises(self):
        with self.assertRaises(PlatformContractError) as ctx:
            verify_sha256(b"hello", "a" * 64)
        self.assertEqual(ctx.exception.code, "PLUGIN_CHECKSUM_MISMATCH")

    def test_case_insensitive_digest(self):
        data = b"hello"
        verify_sha256(data, hashlib.sha256(data).hexdigest().upper())


class ExtractArchiveTests(unittest.TestCase):
    def test_extracts_files(self):
        data = _self_contained_wheel()
        with tempfile.TemporaryDirectory() as tmp:
            extract_archive(data, Path(tmp))
            self.assertTrue((Path(tmp) / "assayer-plugin-release.json").is_file())
            self.assertTrue((Path(tmp) / "test_minimal" / "manifest.json").is_file())

    def test_rejects_path_traversal(self):
        data = _wheel({"../evil.txt": b"x"})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlatformContractError) as ctx:
                extract_archive(data, Path(tmp))
            self.assertEqual(ctx.exception.code, "PLUGIN_ARCHIVE_UNSAFE")

    def test_rejects_absolute_path(self):
        data = _wheel({"/etc/evil.txt": b"x"})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlatformContractError):
                extract_archive(data, Path(tmp))

    def test_rejects_symlink(self):
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(info, "target")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlatformContractError):
                extract_archive(buffer.getvalue(), Path(tmp))


class MaterializeWheelTests(unittest.TestCase):
    def test_materializes_and_returns_root(self):
        data = _self_contained_wheel()
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_wheel(data, digest, Path(tmp))
            self.assertTrue((root / "assayer-plugin-release.json").is_file())

    def test_rejects_wrong_digest(self):
        data = _self_contained_wheel()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlatformContractError) as ctx:
                materialize_wheel(data, "b" * 64, Path(tmp))
            self.assertEqual(ctx.exception.code, "PLUGIN_CHECKSUM_MISMATCH")

    def test_rejects_missing_descriptor(self):
        data = _wheel({"test_minimal/__init__.py": b""})
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlatformContractError) as ctx:
                materialize_wheel(data, digest, Path(tmp))
            self.assertEqual(ctx.exception.code, "PLUGIN_PACKAGE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
