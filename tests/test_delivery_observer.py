import json
import tempfile
import unittest
from pathlib import Path

from assayer_platform import PlatformDeliveryObserver, PlatformRunner, PluginRegistry
from tests.helpers import config_quality_registration


class DeliveryObserverTest(unittest.TestCase):
    def test_runner_uses_unified_delivery_observer_and_observability(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "settings.json"
            source.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            registry = PluginRegistry((config_quality_registration(),))
            result = PlatformRunner(registry, Path(directory) / "output").run(
                plugin_id="test.config-quality", check_id="CFG-001",
                scope={"files": [{"path": str(source)}]}, run_id="run-delivery-observer",
            )
            observed = PlatformDeliveryObserver(Path(directory) / "output" / result.run_id).observe(result)
            self.assertEqual(observed["runId"], result.run_id)
            self.assertTrue((Path(directory) / "output" / result.run_id / f"{result.run_id}.platform-summary.json").is_file())


if __name__ == "__main__":
    unittest.main()
