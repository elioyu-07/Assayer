import json
import subprocess
import sys
import unittest
from pathlib import Path


class ResilienceScanTest(unittest.TestCase):
    def test_repository_resilience_scan_has_no_blocking_findings(self):
        result = subprocess.run([sys.executable, "scripts/resilience_scan.py", "--root", "."], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["blockingCount"], 0)
        self.assertGreaterEqual(report["inventory"]["launch"], 1)


if __name__ == "__main__":
    unittest.main()
