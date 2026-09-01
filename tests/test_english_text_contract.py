import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_english_text.py"
SPEC = importlib.util.spec_from_file_location("check_english_text", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
scan = MODULE.scan


class EnglishTextContractTests(unittest.TestCase):
    def test_authored_text_is_rejected_but_locale_resource_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("English only\n", encoding="utf-8")
            non_english = "\u4e2d\u6587\u8bf4\u660e"
            reset_label = "\u91cd\u7f6e"
            (root / "bad.md").write_text(non_english + "\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "assayer_host").mkdir()
            (root / "src" / "assayer_host" / "locale_terms.py").write_text(f"RESET = '{reset_label}'\n", encoding="utf-8")
            findings = scan(root)
            self.assertEqual([("bad.md", 1, non_english)], findings)


if __name__ == "__main__":
    unittest.main()
