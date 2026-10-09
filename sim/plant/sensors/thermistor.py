import math

from .sensor import Sensor, FaultMode
from sim.plant.electrical import VCC, NTC_R0, NTC_T0, NTC_B, R_DIVIDER


class Thermistor(Sensor):
    """サーミスタ: NTCサーミスタにより水温を電気信号に変換する。

    NTC特性: R(T) = R0 * exp(B * (1/T - 1/T0))
    温度が上がると抵抗値が下がる（負の温度係数）。

    分圧回路構成:
        Vcc --- R_DIVIDER --- [出力端子] --- R_thermistor --- GND

    出力電圧: V = Vcc * R_th / (R_DIVIDER + R_th)
    高温→低抵抗→低電圧、低温→高抵抗→高電圧。

    故障モード:
        OPEN:  サーミスタ断線 → R=∞ → V=Vcc (低温側に張り付く)
        SHORT: サーミスタ短絡 → R=0 → V=0V (高温側に張り付く)
    """

    def __init__(self, initial_temperature: float = 25.0):
        super().__init__("thermistor")
        self._temperature: float = initial_temperature

    def read(self) -> float:
        """現在の水温（℃）を返す（プラント内部用）。"""
        return self._temperature

    def read_resistance(self) -> float:
        """サーミスタの抵抗値 (Ω) を返す。

        NTC特性: R = R0 * exp(B * (1/T - 1/T0))
        """
        t_kelvin = self._temperature + 273.15
        return NTC_R0 * math.exp(NTC_B * (1.0 / t_kelvin - 1.0 / NTC_T0))

    def read_voltage(self) -> float:
        """分圧回路の出力電圧 (V) を返す。

        V = Vcc * R_th / (R_DIVIDER + R_th)

        故障時:
            OPEN:  R_th = ∞ → V = Vcc
            SHORT: R_th = 0 → V = 0V
        """
        if self._fault_mode == FaultMode.OPEN:
            return VCC
        if self._fault_mode == FaultMode.SHORT:
            return 0.0
        r_th = self.read_resistance()
        return VCC * r_th / (R_DIVIDER + r_th)

    def _update(self, temperature: float) -> None:
        """水温を更新する（プラントモデルから呼ばれる）。"""
        self._temperature = temperature
