"""Strict models and shared constants for simulation test cases."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ComparisonOperator(str, Enum):
    EQ = "=="
    NE = "!="
    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="
    IN_RANGE = "in_range"


class ActionType(str, Enum):
    ADD_WATER = "add_water"
    SET_WATER = "set_water"
    LID_OPEN = "lid_open"
    LID_CLOSE = "lid_close"
    BOIL = "boil"
    KEEP_WARM = "keep_warm"
    TIMER = "timer"
    UNLOCK = "unlock"
    DISPENSE_PRESS = "dispense_press"
    DISPENSE_RELEASE = "dispense_release"
    INJECT_FAULT = "inject_fault"
    CLEAR_ALL_FAULTS = "clear_all_faults"


ACTION_TYPES = frozenset(action.value for action in ActionType)
COMPARISON_OPERATORS = frozenset(operator.value for operator in ComparisonOperator)
SENSOR_FAULT_TARGETS = frozenset({
    "thermistor", "water_level_1", "water_level_2", "water_level_3",
    "water_level_4", "full_water", "lid_sensor",
})
ACTUATOR_FAULT_TARGETS = frozenset({"heater", "pump"})
POWER_SUPPLY_FAULT_TARGET = "power_supply"
FAULT_MODES_BY_TARGET = {
    **{target: frozenset({"normal", "open", "short"}) for target in SENSOR_FAULT_TARGETS},
    **{target: frozenset({"normal", "open", "short_on"}) for target in ACTUATOR_FAULT_TARGETS},
    POWER_SUPPLY_FAULT_TARGET: frozenset({"normal", "stuck_on", "stuck_off"}),
}

STATE_FIELDS = frozenset({
    "state", "temperature", "temperature_display", "water_volume_ml",
    "keep_warm_mode", "target_temperature", "water_level_indicators",
    "lid_closed", "heater_on", "heater_duty", "power_on", "pump_on",
    "boiling_lamp", "keep_warm_lamp", "lock_lamp", "locked", "dispensing",
    "timer_display", "timer_running", "buzzer_active", "error",
    "high_temp_error", "temp_not_rising_error", "app.temperature",
    "app.temperature_display", "app.water_level", "app.lid_closed",
    "app.heater_active", "app.heater_duty", "app.pump_on", "app.power_on",
    "plant.temperature", "plant.water_level", "plant.lid_closed",
    "plant.heater_on", "plant.heater_duty", "plant.pump_on", "plant.power_on",
})


class Setup(StrictModel):
    water_ml: float | None = None
    water_temperature: float | None = None
    lid_closed: bool | None = None


class Condition(StrictModel):
    field: str
    op: str
    value: Any


class TimeTrigger(StrictModel):
    type: Literal["time"]
    seconds: float


class WaitTrigger(StrictModel):
    type: Literal["wait"]
    condition: Condition


Trigger = TimeTrigger | WaitTrigger


class Action(StrictModel):
    type: str
    value: float | None = None
    temperature: float | None = None
    target: str | None = None
    mode: str | None = None


class Assertion(StrictModel):
    field: str
    op: str
    expected: Any


class Step(StrictModel):
    description: str = ""
    trigger: Trigger = Field(discriminator="type")
    actions: list[Action] = Field(default_factory=list)
    assertions: list[Assertion] = Field(default_factory=list)


class Scenario(StrictModel):
    name: str
    description: str = ""
    speed: float | None = None
    timeout: float = 600.0
    setup: Setup = Field(default_factory=Setup)
    steps: list[Step]


class TestCase(StrictModel):
    scenarios: list[Scenario]


class TestCaseInput(StrictModel):
    """Accept both the current suite shape and the legacy single-scenario shape."""

    scenarios: list[Scenario] | None = None
    name: str | None = None
    description: str = ""
    speed: float | None = None
    timeout: float = 600.0
    setup: Setup = Field(default_factory=Setup)
    steps: list[Step] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> TestCaseInput:
        if self.scenarios is None and self.steps is None:
            raise ValueError("provide either scenarios or legacy steps")
        if self.scenarios is not None and self.steps is not None:
            raise ValueError("scenarios and legacy steps cannot be combined")
        if self.steps is not None and self.name is None:
            raise ValueError("legacy test cases require name")
        return self

    def normalized(self) -> TestCase:
        if self.scenarios is not None:
            return TestCase(scenarios=self.scenarios)
        return TestCase(scenarios=[Scenario(
            name=self.name or "",
            description=self.description,
            speed=self.speed,
            timeout=self.timeout,
            setup=self.setup,
            steps=self.steps or [],
        )])