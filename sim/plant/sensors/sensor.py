from enum import Enum


class FaultMode(Enum):
    """センサ共通の故障モード。"""
    NORMAL = "normal"   # 正常
    OPEN = "open"       # 断線 (出力がプルアップ電圧に張り付く)
    SHORT = "short"     # GND短絡 (出力が0Vに張り付く)


class Sensor:
    """プラントモデル内のセンサー基底クラス。

    すべてのセンサは read_voltage() で電圧信号を出力する。
    read() は後方互換性のためにプラント内部で使用する。

    故障注入:
        inject_fault(FaultMode) で故障を注入し、
        clear_fault() で正常に戻す。
    """

    def __init__(self, name: str):
        self.name = name
        self._fault_mode: FaultMode = FaultMode.NORMAL

    @property
    def fault_mode(self) -> FaultMode:
        return self._fault_mode

    def inject_fault(self, mode: FaultMode) -> None:
        """故障を注入する。"""
        self._fault_mode = mode

    def clear_fault(self) -> None:
        """故障をクリアして正常に戻す。"""
        self._fault_mode = FaultMode.NORMAL

    def read(self):
        raise NotImplementedError

    def read_voltage(self) -> float:
        """センサ出力電圧 (V) を返す。"""
        raise NotImplementedError
