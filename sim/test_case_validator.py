"""Validation of test-case JSON before a simulation is accepted for execution."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from sim.test_case_models import (
    ACTION_TYPES,
    COMPARISON_OPERATORS,
    FAULT_MODES_BY_TARGET,
    STATE_FIELDS,
    ActionType,
    TestCase,
    TestCaseInput,
)


def _field_path(location: tuple[object, ...]) -> str:
    return "$" + "".join(
        f"[{item}]" if isinstance(item, int) else f".{item}"
        for item in location
    )


def _error(code: str, field: str, message: str, received: Any = None) -> dict[str, Any]:
    return {"code": code, "field": field, "message": message, "received": received}


def _validate_comparison(field: str, op: str, expected: Any, errors: list[dict[str, Any]]) -> None:
    if op not in COMPARISON_OPERATORS:
        errors.append(_error("INVALID_OPERATOR", field, f"unsupported comparison operator: {op}", op))
        return
    if op in {">", "<", ">=", "<="} and (isinstance(expected, bool) or not isinstance(expected, (int, float))):
        errors.append(_error("INVALID_VALUE", field, f"{op} requires a numeric value", expected))
    if op == "in_range":
        valid_range = (
            isinstance(expected, list)
            and len(expected) == 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in expected)
        )
        if not valid_range:
            errors.append(_error("INVALID_VALUE", field, "in_range requires [minimum, maximum] numbers", expected))


def validate_test_case(test_case: Any) -> dict[str, Any]:
    """Return a normalized test case or all structural and semantic validation errors."""
    try:
        parsed = TestCaseInput.model_validate(test_case)
    except ValidationError as exception:
        return {
            "valid": False,
            "errors": [
                _error("INVALID_SCHEMA", _field_path(error["loc"]), error["msg"], error.get("input"))
                for error in exception.errors()
            ],
        }

    normalized = parsed.normalized()
    errors: list[dict[str, Any]] = []
    for scenario_index, scenario in enumerate(normalized.scenarios):
        scenario_path = f"$.scenarios[{scenario_index}]"
        if not scenario.name.strip():
            errors.append(_error("INVALID_VALUE", f"{scenario_path}.name", "name must not be empty", scenario.name))
        if scenario.speed is not None and not 0.5 <= scenario.speed <= 20.0:
            errors.append(_error("INVALID_VALUE", f"{scenario_path}.speed", "speed must be between 0.5 and 20", scenario.speed))
        if scenario.timeout <= 0:
            errors.append(_error("INVALID_VALUE", f"{scenario_path}.timeout", "timeout must be greater than zero", scenario.timeout))
        for step_index, step in enumerate(scenario.steps):
            step_path = f"{scenario_path}.steps[{step_index}]"
            if step.trigger.type == "time" and step.trigger.seconds < 0:
                errors.append(_error("INVALID_VALUE", f"{step_path}.trigger.seconds", "seconds must not be negative", step.trigger.seconds))
            if step.trigger.type == "wait":
                condition = step.trigger.condition
                if condition.field not in STATE_FIELDS:
                    errors.append(_error("INVALID_FIELD", f"{step_path}.trigger.condition.field", "unknown state field", condition.field))
                _validate_comparison(f"{step_path}.trigger.condition.value", condition.op, condition.value, errors)
            for action_index, action in enumerate(step.actions):
                action_path = f"{step_path}.actions[{action_index}]"
                if action.type not in ACTION_TYPES:
                    errors.append(_error("INVALID_ACTION", f"{action_path}.type", "unknown action type", action.type))
                if action.type in {ActionType.ADD_WATER.value, ActionType.SET_WATER.value} and action.value is None:
                    errors.append(_error("MISSING_FIELD", f"{action_path}.value", "water actions require value"))
                if action.type == ActionType.INJECT_FAULT.value:
                    if action.target not in FAULT_MODES_BY_TARGET:
                        errors.append(_error("INVALID_FAULT_TARGET", f"{action_path}.target", "unknown fault target", action.target))
                    elif action.mode not in FAULT_MODES_BY_TARGET[action.target]:
                        errors.append(_error("INVALID_FAULT_MODE", f"{action_path}.mode", "mode is not valid for target", action.mode))
            for assertion_index, assertion in enumerate(step.assertions):
                assertion_path = f"{step_path}.assertions[{assertion_index}]"
                if assertion.field not in STATE_FIELDS:
                    errors.append(_error("INVALID_FIELD", f"{assertion_path}.field", "unknown state field", assertion.field))
                _validate_comparison(f"{assertion_path}.expected", assertion.op, assertion.expected, errors)

    if errors:
        return {"valid": False, "errors": errors}
    return {
        "valid": True,
        "errors": [],
        "normalized_test_case": normalized.model_dump(mode="json", exclude_none=True),
        "summary": {"scenario_count": len(normalized.scenarios), "step_count": sum(len(scenario.steps) for scenario in normalized.scenarios)},
    }