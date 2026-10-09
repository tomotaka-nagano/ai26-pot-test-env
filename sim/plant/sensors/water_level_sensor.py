from .sensor import Sensor, FaultMode
from sim.plant.electrical import VCC


class WaterLevelSensor(Sensor):
    """水位センサ: フロートスイッチにより水位を電気信号に変換する。

    アクティブLOW回路:
        Vcc --- R_pullup --- [出力端子] --- スイッチ --- GND
        水位がしきい値以上 → スイッチ閉 → 出力 0V (LOW)
        水位がしきい値未満 → スイッチ開 → 出力 Vcc (HIGH)

    仕様書より:
    - 第n水位センサ: onの時、その位置よりも水位が高い
    - 満水センサ: onの時、水位が許容上限を超えている

    故障モード:
        OPEN:  断線 → 常にVcc出力 (OFF判定)
        SHORT: GND短絡 → 常に0V出力 (ON判定)
    """

    def __init__(self, name: str, threshold_ml: float):
        super().__init__(name)
        self.threshold_ml = threshold_ml
        self._on: bool = False

    def read(self) -> bool:
        """センサがONかどうかを返す。True=水位がセンサ位置以上（プラント内部用）。"""
        return self._on

    def read_voltage(self) -> float:
        """出力電圧 (V) を返す。アクティブLOW: ON=0V, OFF=Vcc。

        故障時:
            OPEN:  断線 → Vcc (プルアップ)
            SHORT: GND短絡 → 0V
        """
        if self._fault_mode == FaultMode.OPEN:
            return VCC
        if self._fault_mode == FaultMode.SHORT:
            return 0.0
        return 0.0 if self._on else VCC

    def _update(self, water_volume_ml: float) -> None:
        """水量に基づいてセンサ状態を更新する。"""
        self._on = water_volume_ml >= self.threshold_ml
