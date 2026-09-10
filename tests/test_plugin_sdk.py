from __future__ import annotations

import json
import math
from types import MappingProxyType
import unittest

from assayer_platform.contract import PlatformContractError
from assayer_platform.plugin_sdk import (
    PluginContractError,
    to_json_value,
    validate_entity_id,
)


class PluginSdkTests(unittest.TestCase):
    def test_entity_id_accepts_the_shared_platform_form(self):
        for value in ("abc", "finding:one", "Plugin.name-1_2"):
            with self.subTest(value=value):
                self.assertEqual(validate_entity_id(value), value)

    def test_entity_id_rejects_invalid_values_with_a_stable_error(self):
        for value in (None, "ab", "1finding", "中文缺陷编号", "finding id"):
            with self.subTest(value=value):
                with self.assertRaises(PluginContractError) as rejected:
                    validate_entity_id(
                        value, label="finding_id", code="SPEC_REVIEW_INVALID",
                    )
                self.assertIsInstance(rejected.exception, PlatformContractError)
                self.assertEqual(rejected.exception.code, "SPEC_REVIEW_INVALID")
                self.assertEqual(
                    rejected.exception.message,
                    "finding_id must satisfy the platform EntityId contract",
                )

    def test_json_value_detaches_nested_frozen_contract_values(self):
        source = MappingProxyType({
            "review": MappingProxyType({
                "items": (MappingProxyType({"ok": True}), None, 3, 1.5),
            }),
        })

        result = to_json_value(source)

        self.assertEqual(result, {
            "review": {"items": [{"ok": True}, None, 3, 1.5]},
        })
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result["review"]["items"], list)
        json.dumps(result, allow_nan=False)

    def test_json_value_sorts_sets_by_canonical_json(self):
        value = frozenset({"z", "a", 10, 2})

        first = to_json_value(value)
        second = to_json_value(value)

        self.assertEqual(first, second)
        self.assertEqual(
            first,
            sorted(first, key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )),
        )

    def test_json_value_rejects_non_string_keys_instead_of_coercing(self):
        with self.assertRaises(PluginContractError) as rejected:
            to_json_value({1: "numeric", "1": "string"}, label="Plugin summary")

        self.assertEqual(rejected.exception.errors[0]["keyword"], "propertyNames")
        self.assertNotIn("dict", rejected.exception.message)

    def test_json_value_rejects_unknown_objects_without_leaking_class_names(self):
        class InternalSecretType:
            pass

        with self.assertRaises(PluginContractError) as rejected:
            to_json_value(
                {"invalid": InternalSecretType()}, label="Plugin summary",
            )

        self.assertEqual(rejected.exception.errors[0]["pointer"], "/invalid")
        self.assertNotIn("InternalSecretType", rejected.exception.message)
        self.assertNotIn("InternalSecretType", str(rejected.exception.errors))

    def test_json_value_rejects_non_finite_numbers(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(PluginContractError) as rejected:
                    to_json_value({"number": value})
                self.assertEqual(
                    rejected.exception.errors[0]["keyword"], "finiteNumber",
                )

    def test_json_value_rejects_cycles(self):
        value: list[object] = []
        value.append(value)

        with self.assertRaises(PluginContractError) as rejected:
            to_json_value(value)

        self.assertEqual(rejected.exception.errors[0]["keyword"], "acyclic")


if __name__ == "__main__":
    unittest.main()
