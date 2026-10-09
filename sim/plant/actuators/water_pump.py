from .actuator import Actuator


class WaterPump(Actuator):
    """ポンプ: ポット内の水を吸い上げて給湯口から排出する。

    仕様書より:
    - ポット内の水を吸い上げて給湯口から排出する
    - 給湯ボタンを押している間中、給湯を行う
    """

    def __init__(self, flow_rate_ml_per_sec: float = 15.0):
        super().__init__("water_pump")
        self._flow_rate_ml_per_sec = flow_rate_ml_per_sec

    @property
    def flow_rate(self) -> float:
        """流量（ml/秒）を返す。"""
        return self._flow_rate_ml_per_sec

    def get_output_flow(self) -> float:
        """現在の排出流量（ml/秒）を返す。"""
        if not self._on:
            return 0.0
        return self._flow_rate_ml_per_sec
