"""Unit tests for MCP test-case validation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from sim.test_case_validator import validate_test_case


class TestCaseValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = Path(__file__).parent / "product" / "sample_tests.json"
        self.sample = json.loads(fixture.read_text(encoding="utf-8"))

    def test_accepts_current_suite_format(self) -> None:
        result = validate_test_case(self.sample)
        self.assertTrue(result["valid"])
        self.assertEqual(result["summary"]["scenario_count"], 7)

    def test_accepts_legacy_single_scenario_format(self) -> None:
        result = validate_test_case(self.sample["scenarios"][0])
        self.assertTrue(result["valid"])
        self.assertEqual(result["summary"]["scenario_count"], 1)

    def test_reports_semantic_errors_with_json_paths(self) -> None:
        invalid = {
            "scenarios": [{
                "name": "bad",
                "timeout": -1,
                "steps": [{
                    "trigger": {"type": "wait", "condition": {"field": "missing", "op": "==", "value": 1}},
                    "actions": [{"type": "inject_fault", "target": "heater", "mode": "short"}],
                    "assertions": [],
                }],
            }],
        }
        result = validate_test_case(invalid)
        self.assertFalse(result["valid"])
        fields = {error["field"] for error in result["errors"]}
        self.assertIn("$.scenarios[0].timeout", fields)
        self.assertIn("$.scenarios[0].steps[0].trigger.condition.field", fields)
        self.assertIn("$.scenarios[0].steps[0].actions[0].mode", fields)

    def test_reports_structural_errors_without_raising(self) -> None:
        result = validate_test_case({"scenarios": "not-an-array"})
        self.assertFalse(result["valid"])
        self.assertEqual(result["errors"][0]["code"], "INVALID_SCHEMA")


if __name__ == "__main__":
    unittest.main()