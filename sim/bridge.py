"""Python ↔ C++ ブリッジモジュール。

sim/ のPython植物モデルと src_cpp/ のC++制御アプリケーションを接続する。
Python側のセンサ/アクチュエータを C++ ISensorInput/IActuatorOutput/IDutyCycleOutput
インターフェースでラップし、C++ PotController に渡す。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from sim.native_loader import ensure_pot_cpp_loaded

pot_cpp = ensure_pot_cpp_loaded()

if TYPE_CHECKING:
    from sim.plant.pot import Pot


# ---------------------------------------------------------------
# IO アダプタ: Python sensor/actuator → C++ interface
# ---------------------------------------------------------------

class SensorAdapter(pot_cpp.ISensorInput):
    """Python Sensor.read_voltage() → C++ ISensorInput.read_voltage()"""

    def __init__(self, sensor):
        super().__init__()
        self._sensor = sensor

    def read_voltage(self) -> float:
        return self._sensor.read_voltage()


class ActuatorAdapter(pot_cpp.IActuatorOutput):
    """Python Actuator.set_control_voltage() → C++ IActuatorOutput"""

    def __init__(self, actuator):
        super().__init__()
        self._actuator = actuator

    def set_control_voltage(self, voltage: float) -> None:
        self._actuator.set_control_voltage(voltage)


class DutyCycleAdapter(pot_cpp.IDutyCycleOutput):
    """Python Heater.set_duty_cycle() → C++ IDutyCycleOutput"""

    def __init__(self, actuator):
        super().__init__()
        self._actuator = actuator

    def set_duty_cycle(self, duty_percent: float) -> None:
        self._actuator.set_duty_cycle(duty_percent)


# ---------------------------------------------------------------
# Python Enum 互換
# ---------------------------------------------------------------

class PotState(Enum):
    """C++ PotState 互換の Python Enum。"""
    IDLE = "idle"
    BOILING = "boiling"
    KEEP_WARM = "keep_warm"
    ERROR = "error"


class KeepWarmMode(Enum):
    """C++ KeepWarmMode 互換の Python Enum。"""
    HIGH = "high"
    ECONOMY = "economy"
    MILK = "milk"


_CPP_STATE_MAP = {
    pot_cpp.PotState.IDLE: PotState.IDLE,
    pot_cpp.PotState.BOILING: PotState.BOILING,
    pot_cpp.PotState.KEEP_WARM: PotState.KEEP_WARM,
    pot_cpp.PotState.ERROR: PotState.ERROR,
}

_CPP_MODE_MAP = {
    pot_cpp.KeepWarmMode.HIGH: KeepWarmMode.HIGH,
    pot_cpp.KeepWarmMode.ECONOMY: KeepWarmMode.ECONOMY,
    pot_cpp.KeepWarmMode.MILK: KeepWarmMode.MILK,
}


# ---------------------------------------------------------------
# ブリッジ内部のIO状態ラッパー（web/app.py 互換用）
# ---------------------------------------------------------------

class _LidInputProxy:
    def __init__(self, cpp_ctrl):
        self._cpp = cpp_ctrl

    def is_active(self) -> bool:
        return self._cpp.app_lid_closed


class _HeaterDutyProxy:
    def __init__(self, cpp_ctrl):
        self._cpp = cpp_ctrl

    @property
    def duty(self) -> float:
        return self._cpp.app_heater_duty


class _PumpOutputProxy:
    def __init__(self, cpp_ctrl):
        self._cpp = cpp_ctrl

    @property
    def is_high(self) -> bool:
        return self._cpp.app_pump_on


class _PowerSupplyOutputProxy:
    def __init__(self, cpp_ctrl):
        self._cpp = cpp_ctrl

    @property
    def is_high(self) -> bool:
        return self._cpp.app_power_on


# ---------------------------------------------------------------
# PotController ブリッジ
# ---------------------------------------------------------------

class PotController:
    """C++ PotController を Python PotController と同じインターフェースで提供する。

    web/app.py と tests/scenario_runner.py からそのまま使える。
    """

    BOILING_TARGET = 100.0
    BOILING_HOLD_TIME = 180.0

    def __init__(self, pot: Pot):
        self.pot = pot

        # アダプタ（C++ が参照を保持するので、寿命を管理する）
        self._adapters: list = []

        temp_a = SensorAdapter(pot.thermistor)
        wl_adapters = [SensorAdapter(s) for s in pot.water_level_sensors]
        full_a = SensorAdapter(pot.full_water_sensor)
        lid_a = SensorAdapter(pot.lid_sensor)
        heater_relay_a = ActuatorAdapter(pot.heater)
        heater_pwm_a = DutyCycleAdapter(pot.heater)
        pump_a = ActuatorAdapter(pot.pump)
        ps_a = ActuatorAdapter(pot.heater_power_supply)

        self._adapters.extend([
            temp_a, *wl_adapters, full_a, lid_a,
            heater_relay_a, heater_pwm_a, pump_a, ps_a,
        ])

        self._cpp = pot_cpp.PotController(
            temp_a,
            wl_adapters,
            full_a,
            lid_a,
            heater_relay_a,
            heater_pwm_a,
            pump_a,
            ps_a,
        )

        # web/app.py 互換プロキシ
        self._lid_input = _LidInputProxy(self._cpp)
        self._heater_duty = _HeaterDutyProxy(self._cpp)
        self._pump_output = _PumpOutputProxy(self._cpp)
        self._power_supply_output = _PowerSupplyOutputProxy(self._cpp)

    # ---------------------------------------------------------------
    # Properties（元の Python PotController と同じ）
    # ---------------------------------------------------------------

    @property
    def state(self) -> PotState:
        return _CPP_STATE_MAP[self._cpp.state]

    @property
    def keep_warm_mode(self) -> KeepWarmMode:
        return _CPP_MODE_MAP[self._cpp.keep_warm_mode]

    @property
    def locked(self) -> bool:
        return self._cpp.locked

    @property
    def dispensing(self) -> bool:
        return self._cpp.dispensing

    @property
    def target_temperature(self) -> float:
        return self._cpp.target_temperature

    @property
    def current_temperature_reading(self) -> float:
        return self._cpp.current_temperature_reading

    @property
    def water_level_readings(self) -> list[bool]:
        return self._cpp.water_level_readings

    @property
    def is_heater_active(self) -> bool:
        return self._cpp.is_heater_active

    # Sub-modules
    @property
    def error_detector(self):
        return self._cpp.error_detector

    @property
    def timer(self):
        return self._cpp.timer

    @property
    def buzzer(self):
        return self._cpp.buzzer

    @property
    def display(self):
        return self._cpp.display

    # ---------------------------------------------------------------
    # Button handlers
    # ---------------------------------------------------------------

    def on_boil_button(self) -> None:
        self._cpp.on_boil_button()

    def on_keep_warm_button(self) -> None:
        self._cpp.on_keep_warm_button()

    def on_timer_button(self) -> None:
        self._cpp.on_timer_button()

    def on_unlock_button(self) -> None:
        self._cpp.on_unlock_button()

    def on_dispense_press(self) -> None:
        self._cpp.on_dispense_press()

    def on_dispense_release(self) -> None:
        self._cpp.on_dispense_release()

    # ---------------------------------------------------------------
    # Update
    # ---------------------------------------------------------------

    def update(self, dt: float) -> None:
        self._cpp.update(dt)
