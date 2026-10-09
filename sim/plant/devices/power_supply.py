from enum import Enum

from sim.plant.electrical import LOGIC_HIGH_THRESHOLD


class PowerSupplyFaultMode(Enum):
    """電源の故障モード。"""
    NORMAL = "normal"       # 正常
    STUCK_ON = "stuck_on"   # 固着ON (遮断不能)
    STUCK_OFF = "stuck_off" # 固着OFF (常時遮断)


class PowerSupply:
    """ヒータ用電源: ヒータへ電力を供給する。

    制御電圧入力を持ち、HIGH=通電、LOW=遮断。

    仕様書より:
    - 通常はON
    - ヒータに異常が発生した時にOFFにして電力を遮断する

    故障モード:
        STUCK_ON:  固着ON → 制御信号に関わらず常に通電
        STUCK_OFF: 固着OFF → 制御信号に関わらず常に遮断
    """

    def __init__(self):
        self._on: bool = True  # 通常はON
        self._control_voltage: float = 0.0
        self._fault_mode: PowerSupplyFaultMode = PowerSupplyFaultMode.NORMAL

    @property
    def fault_mode(self) -> PowerSupplyFaultMode:
        return self._fault_mode

    def inject_fault(self, mode: PowerSupplyFaultMode) -> None:
        """故障を注入する。"""
        self._fault_mode = mode

    def clear_fault(self) -> None:
        """故障をクリアして正常に戻す。"""
        self._fault_mode = PowerSupplyFaultMode.NORMAL

    def set_control_voltage(self, voltage: float) -> None:
        """制御入力電圧を設定する。しきい値を超えると通電する。"""
        self._control_voltage = voltage
        self._on = voltage > LOGIC_HIGH_THRESHOLD

    def turn_on(self) -> None:
        self._on = True

    def turn_off(self) -> None:
        """電力を遮断する。"""
        self._on = False

    @property
    def is_on(self) -> bool:
        """電源が実際に通電中かどうか。故障状態を考慮する。"""
        if self._fault_mode == PowerSupplyFaultMode.STUCK_ON:
            return True
        if self._fault_mode == PowerSupplyFaultMode.STUCK_OFF:
            return False
        return self._on

    @property
    def control_voltage(self) -> float:
        return self._control_voltage
        return self._control_voltage
