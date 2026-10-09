"""Tests for the production synchronous simulation executor."""

from __future__ import annotations

import unittest
from threading import Event

from sim.scenario_runner import ScenarioRunner, ScenarioSuiteRunner


SCENARIO = {
    "name": "long-running",
    "timeout": 600,
    "steps": [{
        "trigger": {"type": "wait", "condition": {"field": "state", "op": "==", "value": "boiling"}},
        "actions": [],
        "assertions": [],
    }],
}


class ScenarioExecutorTests(unittest.TestCase):
    def test_pre_cancelled_scenario_stops_before_first_tick(self) -> None:
        cancel_event = Event()
        cancel_event.set()

        result = ScenarioRunner.run_scenario_sync(SCENARIO, cancel_event=cancel_event)

        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["elapsed_time"], 0.0)

    def test_expired_wall_deadline_is_reported_as_execution_timeout(self) -> None:
        result = ScenarioRunner.run_scenario_sync(SCENARIO, deadline_seconds=0)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "EXECUTION_TIMEOUT")

    def test_suite_stops_after_cancelled_scenario(self) -> None:
        cancel_event = Event()
        cancel_event.set()

        result = ScenarioSuiteRunner.run_suite_sync({"scenarios": [SCENARIO, SCENARIO]}, cancel_event=cancel_event)

        self.assertEqual(len(result["scenarios"]), 1)
        self.assertEqual(result["scenarios"][0]["status"], "stopped")


if __name__ == "__main__":
    unittest.main()