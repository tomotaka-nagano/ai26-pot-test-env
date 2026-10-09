from enum import Enum

from sim.plant.electrical import LOGIC_HIGH_THRESHOLD


class ActuatorFaultMode(Enum):
    """アクチュエータの故障モード。"""
    NORMAL = "normal"       # 正常
    OPEN = "open"           # 断線 (制御信号が届かない、常にOFF)
    SHORT_ON = "short_on"   # 固着ON (制御不能、常にON)


class Actuator:
    """プラントモデル内のアクチュエータ基底クラス。

    制御電圧入力を持ち、しきい値 (Vcc/2) を超えるとONになる。
    turn_on()/turn_off() は後方互換性のために残す。

    故障注入:
        inject_fault(ActuatorFaultMode) で故障を注入し、
        clear_fault() で正常に戻す。
    """

    def __init__(self, name: str):
        self.name = name
        self._on: bool = False
        self._control_voltage: float = 0.0
        self._fault_mode: ActuatorFaultMode = ActuatorFaultMode.NORMAL

    @property
    def fault_mode(self) -> ActuatorFaultMode:
        return self._fault_mode

    def inject_fault(self, mode: ActuatorFaultMode) -> None:
        """故障を注入する。"""
        self._fault_mode = mode

    def clear_fault(self) -> None:
        """故障をクリアして正常に戻す。"""
        self._fault_mode = ActuatorFaultMode.NORMAL

    def set_control_voltage(self, voltage: float) -> None:
        """制御入力電圧を設定する。しきい値を超えるとONになる。"""
        self._control_voltage = voltage
        self._on = voltage > LOGIC_HIGH_THRESHOLD

    def turn_on(self) -> None:
        self._on = True

    def turn_off(self) -> None:
        self._on = False

    @property
    def is_on(self) -> bool:
        """アクチュエータが実際に動作中かどうか。故障状態を考慮する。"""
        if self._fault_mode == ActuatorFaultMode.OPEN:
            return False
        if self._fault_mode == ActuatorFaultMode.SHORT_ON:
            return True
        return self._on

    @property
    def control_voltage(self) -> float:
        return self._control_voltage
