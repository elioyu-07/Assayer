import tempfile
import unittest
from pathlib import Path

from scripts.resilience_scan import scan


class ResilienceScanTest(unittest.TestCase):
    def test_scan_distinguishes_clean_code_from_each_blocking_pattern(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            source.mkdir()
            (source / "clean.py").write_text(
                "def bounded(items):\n    for item in items:\n        yield item\n",
                encoding="utf-8",
            )
            self.assertEqual(scan(root)["status"], "pass")

            (source / "unsafe.py").write_text(
                "def unsafe(browser):\n"
                "    try:\n"
                "        browser.launch()\n"
                "    except:\n"
                "        pass\n"
                "    while True:\n"
                "        break\n",
                encoding="utf-8",
            )
            report = scan(root)

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["blockingCount"], 4)
        self.assertEqual(
            {item["rule"] for item in report["findings"]},
            {
                "S-BARE-EXCEPT",
                "S-EMPTY-CATCH",
                "S-MISSING-TIMEOUT",
                "S-UNBOUNDED-LOOP",
            },
        )


if __name__ == "__main__":
    unittest.main()
