from .sensor import Sensor, FaultMode
from sim.plant.electrical import VCC


class LidSensor(Sensor):
    """蓋センサ: 接点型スイッチにより蓋の開閉を電気信号に変換する。

    アクティブLOW回路:
        Vcc --- R_pullup --- [出力端子] --- スイッチ --- GND
        蓋が閉じている → スイッチ閉 → 出力 0V (LOW)
        蓋が開いている → スイッチ開 → 出力 Vcc (HIGH)

    仕様書より: 蓋が閉じている時にONになる。

    故障モード:
        OPEN:  断線 → 常にVcc出力 (蓋が開いていると誤判定)
        SHORT: GND短絡 → 常に0V出力 (蓋が閉じていると誤判定)
    """

    def __init__(self):
        super().__init__("lid_sensor")
        self._closed: bool = True

    def read(self) -> bool:
        """蓋が閉じているかどうかを返す。True=閉じている（プラント内部用）。"""
        return self._closed

    def read_voltage(self) -> float:
        """出力電圧 (V) を返す。アクティブLOW: 閉=0V, 開=Vcc。

        故障時:
            OPEN:  断線 → Vcc (蓋開と誤認)
            SHORT: GND短絡 → 0V (蓋閉と誤認)
        """
        if self._fault_mode == FaultMode.OPEN:
            return VCC
        if self._fault_mode == FaultMode.SHORT:
            return 0.0
        return 0.0 if self._closed else VCC

    def set_closed(self, closed: bool) -> None:
        """蓋の開閉状態を設定する（外部操作）。"""
        self._closed = closed
