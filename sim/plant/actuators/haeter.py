from .actuator import Actuator


class Heater(Actuator):
    """ヒータ: ポット内の水を加熱する。

    仕様書より:
    - 制御周期と操作量（%）によりon/off制御される
    - 操作量75%の場合、制御周期の75%の時間だけヒータがON
    - ヒータ用電源がOFFの場合、動作しない
    """

    def __init__(self, power_watts: float = 700.0):
        super().__init__("heater")
        self._power_watts = power_watts
        self._duty_cycle: float = 0.0  # 操作量 0〜100%

    @property
    def power_watts(self) -> float:
        return self._power_watts

    @property
    def duty_cycle(self) -> float:
        return self._duty_cycle

    def set_duty_cycle(self, duty_cycle: float) -> None:
        """操作量を設定する（0〜100%）。"""
        self._duty_cycle = max(0.0, min(100.0, duty_cycle))

    def get_power_output(self) -> float:
        """現在の出力パワー（W）を返す。操作量に応じた平均出力。"""
        if not self._on:
            return 0.0
        return self._power_watts * self._duty_cycle / 100.0
