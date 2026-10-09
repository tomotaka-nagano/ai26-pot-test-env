class WaterRail:
    """水路: ポンプによって吸い上げられる水の通路。

    物理的な水路をモデル化する。水路内の残留水量を考慮する。
    """

    def __init__(self, capacity_ml: float = 20.0):
        self._capacity_ml = capacity_ml
        self._water_in_rail_ml: float = 0.0

    @property
    def capacity_ml(self) -> float:
        return self._capacity_ml

    @property
    def water_in_rail_ml(self) -> float:
        return self._water_in_rail_ml

    def fill(self, amount_ml: float) -> float:
        """水路に水を流し込む。実際に流れた量を返す。"""
        space = self._capacity_ml - self._water_in_rail_ml
        actual = min(amount_ml, space)
        self._water_in_rail_ml += actual
        return actual

    def drain(self, amount_ml: float) -> float:
        """水路から水を排出する。実際に排出された量を返す。"""
        actual = min(amount_ml, self._water_in_rail_ml)
        self._water_in_rail_ml -= actual
        return actual
