from .sensors.thermistor import Thermistor
from .sensors.water_level_sensor import WaterLevelSensor
from .sensors.lid_sensor import LidSensor
from .actuators.haeter import Heater
from .actuators.water_pump import WaterPump
from .devices.power_supply import PowerSupply
from .mechanicals.water_rail import WaterRail


class Pot:
    """話題沸騰ポット（GOMA-1015型）のプラントモデル。

    ポットの物理的な振る舞いをシミュレートする。
    step() メソッドで時間を進め、各センサ・アクチュエータの状態を更新する。

    物理定数:
        SPECIFIC_HEAT_WATER: 水の比熱 [J/(kg·℃)]
        WATER_DENSITY_KG_PER_ML: 水の密度 [kg/ml]
        LATENT_HEAT_VAPORIZATION: 水の蒸発潜熱 [J/kg]
    """

    SPECIFIC_HEAT_WATER = 4186.0  # J/(kg·℃)
    WATER_DENSITY_KG_PER_ML = 0.001  # kg/ml
    LATENT_HEAT_VAPORIZATION = 2_260_000.0  # J/kg

    # 水位センサの閾値 (ml) — 第1〜第4水位センサ
    WATER_LEVEL_THRESHOLDS = [200.0, 400.0, 600.0, 800.0]
    # 満水センサの閾値 (ml)
    FULL_WATER_THRESHOLD = 1000.0

    def __init__(
        self,
        ambient_temperature: float = 25.0,
        cooling_coefficient: float = 0.0005,
        heater_power_watts: float = 700.0,
        pump_flow_rate_ml_per_sec: float = 15.0,
        capacity_ml: float = 1200.0,
    ):
        """
        Args:
            ambient_temperature: 外気温 (℃)
            cooling_coefficient: 冷却係数 (1/秒), ニュートンの冷却法則の係数
            heater_power_watts: ヒータの定格出力 (W)
            pump_flow_rate_ml_per_sec: ポンプの流量 (ml/秒)
            capacity_ml: ポットの容量 (ml)
        """
        self._ambient_temperature = ambient_temperature
        self._cooling_coefficient = cooling_coefficient
        self._capacity_ml = capacity_ml

        # --- 内部状態 ---
        self._water_volume_ml: float = 0.0
        self._water_temperature: float = ambient_temperature

        # --- センサ ---
        self.thermistor = Thermistor(initial_temperature=ambient_temperature)
        self.water_level_sensors: list[WaterLevelSensor] = [
            WaterLevelSensor(
                f"water_level_{i + 1}", threshold
            )
            for i, threshold in enumerate(self.WATER_LEVEL_THRESHOLDS)
        ]
        self.full_water_sensor = WaterLevelSensor(
            "full_water", self.FULL_WATER_THRESHOLD
        )
        self.lid_sensor = LidSensor()

        # --- アクチュエータ ---
        self.heater = Heater(power_watts=heater_power_watts)
        self.pump = WaterPump(flow_rate_ml_per_sec=pump_flow_rate_ml_per_sec)

        # --- デバイス ---
        self.heater_power_supply = PowerSupply()

        # --- 機構部品 ---
        self.water_rail = WaterRail()

    # ---------------------------------------------------------------
    # 外部操作（ユーザ操作のシミュレーション）
    # ---------------------------------------------------------------

    def add_water(self, volume_ml: float) -> None:
        """ポットに水を注ぐ。水温は加重平均で混合される。"""
        if volume_ml <= 0:
            return
        added_temp = self._ambient_temperature
        if self._water_volume_ml > 0:
            total = self._water_volume_ml + volume_ml
            self._water_temperature = (
                self._water_temperature * self._water_volume_ml
                + added_temp * volume_ml
            ) / total
            self._water_volume_ml = min(total, self._capacity_ml)
        else:
            self._water_volume_ml = min(volume_ml, self._capacity_ml)
            self._water_temperature = added_temp
        self._update_sensors()

    def set_water(self, volume_ml: float, temperature: float | None = None) -> None:
        """ポット内の水量と水温を直接設定する（テスト用）。"""
        self._water_volume_ml = min(max(0.0, volume_ml), self._capacity_ml)
        if temperature is not None:
            self._water_temperature = temperature
        self._update_sensors()

    # ---------------------------------------------------------------
    # 状態取得
    # ---------------------------------------------------------------

    @property
    def capacity_ml(self) -> float:
        """ポットの容量 (ml)。"""
        return self._capacity_ml

    @property
    def water_volume_ml(self) -> float:
        """現在の水量 (ml)。"""
        return self._water_volume_ml

    @property
    def water_temperature(self) -> float:
        """現在の水温 (℃)。"""
        return self._water_temperature

    @property
    def ambient_temperature(self) -> float:
        return self._ambient_temperature

    @property
    def is_heater_effective(self) -> bool:
        """ヒータが実際に通電しているかどうか。
        ヒータON かつ ヒータ用電源ON の場合のみ True。
        """
        return (
            self.heater.is_on
            and self.heater_power_supply.is_on
            and self.heater.duty_cycle > 0
        )

    @property
    def can_control_temperature(self) -> bool:
        """温度制御が可能かどうか。

        仕様書より:
        第n水位センサがONで、かつ満水センサがOFFの場合、温度制御可能。
        """
        any_level_on = any(s.read() for s in self.water_level_sensors)
        full_water_off = not self.full_water_sensor.read()
        return any_level_on and full_water_off

    # ---------------------------------------------------------------
    # シミュレーションステップ
    # ---------------------------------------------------------------

    def step(self, dt: float) -> None:
        """シミュレーションを dt 秒進める。

        以下の物理プロセスを順に計算する:
        1. ヒータによる加熱
        2. ニュートンの冷却法則による自然冷却
        3. 100℃以上での蒸発による水量減少
        4. ポンプによる給湯（排水）
        5. 全センサの状態更新

        Args:
            dt: タイムステップ (秒)
        """
        if dt <= 0:
            return

        self._apply_heating(dt)
        self._apply_cooling(dt)
        self._apply_evaporation(dt)
        self._apply_pumping(dt)
        self._update_sensors()

    def _apply_heating(self, dt: float) -> None:
        """ヒータによる加熱を計算する。"""
        if not self.is_heater_effective:
            return
        if self._water_volume_ml <= 0:
            return

        power_w = self.heater.get_power_output()
        energy_j = power_w * dt
        mass_kg = self._water_volume_ml * self.WATER_DENSITY_KG_PER_ML
        delta_temp = energy_j / (self.SPECIFIC_HEAT_WATER * mass_kg)
        self._water_temperature += delta_temp

    def _apply_cooling(self, dt: float) -> None:
        """ニュートンの冷却法則に基づく自然冷却を計算する。

        dT/dt = -k * (T - T_ambient)
        蓋が開いている場合は冷却係数を大きくする。
        """
        if self._water_volume_ml <= 0:
            return

        k = self._cooling_coefficient
        if not self.lid_sensor.read():
            # 蓋が開いている場合、冷却が速い
            k *= 3.0

        temp_diff = self._water_temperature - self._ambient_temperature
        self._water_temperature -= k * temp_diff * dt

    def _apply_evaporation(self, dt: float) -> None:
        """100℃以上での蒸発を計算する。

        水温が100℃を超えた場合、余剰エネルギーが蒸発に使われ水量が減少する。
        ただしヒータ異常のシミュレーションのため、温度が100℃を大幅に超える
        ことも許容する（高温エラー検知：110℃）。
        """
        if self._water_volume_ml <= 0:
            return
        if self._water_temperature <= 100.0:
            return

        # 100℃超過分のエネルギーの一部で蒸発が起きる
        excess_temp = self._water_temperature - 100.0
        mass_kg = self._water_volume_ml * self.WATER_DENSITY_KG_PER_ML
        excess_energy = excess_temp * self.SPECIFIC_HEAT_WATER * mass_kg

        # 超過エネルギーの一部（蒸発率）を蒸発に使う
        evaporation_rate = 0.3
        evap_energy = excess_energy * evaporation_rate
        evap_mass_kg = evap_energy / self.LATENT_HEAT_VAPORIZATION
        evap_volume_ml = evap_mass_kg / self.WATER_DENSITY_KG_PER_ML

        self._water_volume_ml = max(0.0, self._water_volume_ml - evap_volume_ml)

        # 蒸発により温度が下がる
        if self._water_volume_ml > 0:
            self._water_temperature -= excess_temp * evaporation_rate
        else:
            self._water_temperature = self._ambient_temperature

    def _apply_pumping(self, dt: float) -> None:
        """ポンプによる排水を計算する。"""
        if not self.pump.is_on:
            return
        if self._water_volume_ml <= 0:
            return

        flow = self.pump.get_output_flow() * dt
        # 水路を通して排出
        drawn_from_pot = min(flow, self._water_volume_ml)
        self.water_rail.fill(drawn_from_pot)
        self._water_volume_ml -= drawn_from_pot

        # 水路から給湯口へ排出
        self.water_rail.drain(drawn_from_pot)

    def _update_sensors(self) -> None:
        """全センサの状態を更新する。"""
        self.thermistor._update(self._water_temperature)
        for sensor in self.water_level_sensors:
            sensor._update(self._water_volume_ml)
        self.full_water_sensor._update(self._water_volume_ml)