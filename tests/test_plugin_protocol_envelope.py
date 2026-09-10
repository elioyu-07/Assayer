import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver


ROOT = Path(__file__).resolve().parents[1]


def _validator() -> Draft202012Validator:
    schemas = {}
    for path in (ROOT / "schemas").glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        schemas[schema["$id"]] = schema
    schema = schemas["plugin-protocol-envelope.schema.json"]
    return Draft202012Validator(
        schema,
        resolver=RefResolver(schema["$id"], schema, store=schemas),
    )


class PluginProtocolEnvelopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = _validator()

    def assert_valid(self, document: dict) -> None:
        errors = sorted(self.validator.iter_errors(document), key=str)
        self.assertEqual([], [error.message for error in errors], document)

    def assert_invalid(self, document: dict) -> None:
        self.assertTrue(list(self.validator.iter_errors(document)), document)

    def test_request_envelope_is_valid(self) -> None:
        self.assert_valid({
            "protocolVersion": "1.2.0",
            "requestId": "req-0001",
            "pluginId": "dev.assayer.frontend-audit",
            "runId": "run-0001",
            "checkId": "FUA-10",
            "operation": "inspect",
            "deadlineMs": 30000,
            "input": {},
        })

    def test_ok_response_requires_output(self) -> None:
        self.assert_valid({
            "protocolVersion": "1.2.0",
            "requestId": "req-0001",
            "status": "ok",
            "output": {"packets": []},
        })
        self.assert_invalid({
            "protocolVersion": "1.2.0",
            "requestId": "req-0001",
            "status": "ok",
        })

    def test_rejected_response_requires_error(self) -> None:
        self.assert_valid({
            "protocolVersion": "1.2.0",
            "requestId": "req-0001",
            "status": "rejected",
            "error": {
                "code": "PLUGIN_CONTRACT_VIOLATION",
                "message": "Inspect input is missing the frozen Check identity",
                "retryable": False,
                "requiredNextStep": "Retry with the frozen Check identity",
            },
        })
        self.assert_invalid({
            "protocolVersion": "1.2.0",
            "requestId": "req-0001",
            "status": "rejected",
        })

    def test_request_requires_a_known_operation(self) -> None:
        base = {
            "protocolVersion": "1.2.0",
            "requestId": "req-0001",
            "pluginId": "dev.assayer.frontend-audit",
            "input": {},
        }
        self.assert_valid({**base, "operation": "discover"})
        self.assert_invalid({**base, "operation": "execute_arbitrary_code"})
        self.assert_invalid({**base})

    def test_error_shape_is_enforced(self) -> None:
        self.assert_invalid({
            "protocolVersion": "1.2.0",
            "requestId": "req-0001",
            "status": "failed",
            "error": {"code": "PLUGIN_FAILED", "message": "boom", "retryable": True},
        })


if __name__ == "__main__":
    unittest.main()
