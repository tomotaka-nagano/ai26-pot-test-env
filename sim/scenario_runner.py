"""自動テストシナリオ実行エンジン。

JSON形式で定義されたテストシナリオを読み込み、
シミュレーション上で自動実行し、判定結果を返す。

トリガータイプ:
  - time: 指定時刻（秒）に達したら発火
  - wait: 条件が満たされるまで待機（タイムアウト付き）

アクションタイプ:
  - add_water / set_water: 注水
  - lid_open / lid_close: 蓋操作
  - boil / keep_warm / timer / unlock: ボタン操作
  - dispense_press / dispense_release: 給湯操作
  - inject_fault / clear_all_faults: 故障注入

アサーション演算子:
  - ==, !=, >, <, >=, <=: 基本比較
  - in_range: 範囲内 [min, max]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from time import monotonic
from typing import Any

from sim.plant.pot import Pot
from sim.plant.sensors.sensor import FaultMode
from sim.plant.actuators.actuator import ActuatorFaultMode
from sim.plant.devices.power_supply import PowerSupplyFaultMode
from sim.bridge import PotController


class StepStatus(Enum):
    """テストステップの状態。"""

    PENDING = "pending"
    WAITING = "waiting"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class AssertionResult:
    """個別アサーションの結果。"""

    field: str
    op: str
    expected: Any
    actual: Any
    passed: bool
    message: str = ""


@dataclass
class StepResult:
    """テストステップの結果。"""

    step_index: int
    description: str
    status: StepStatus
    assertions: list[AssertionResult] = field(default_factory=list)
    triggered_at: float | None = None


@dataclass
class ScenarioResult:
    """テストシナリオ全体の結果。"""

    name: str
    description: str
    status: str = "loaded"
    steps: list[StepResult] = field(default_factory=list)
    elapsed_time: float = 0.0


class ScenarioRunner:
    """テストシナリオ実行エンジン。

    JSONで定義されたテストシナリオを読み込み、
    シミュレーションのステップごとにトリガーを評価し、
    アクションの実行とアサーションの検証を行う。
    """

    def __init__(self, pot: Pot, controller: PotController) -> None:
        self.pot = pot
        self.controller = controller
        self._scenario: dict | None = None
        self._steps: list[dict] = []
        self._current_step_index: int = 0
        self._result: ScenarioResult | None = None
        self._running: bool = False
        self._elapsed: float = 0.0
        self._timeout: float = 600.0
        self._speed: float | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def result(self) -> ScenarioResult | None:
        return self._result

    @property
    def preferred_speed(self) -> float | None:
        """シナリオで指定されたシミュレーション速度。"""
        return self._speed

    def load(self, scenario: dict) -> None:
        """シナリオをロードする。"""
        self._scenario = scenario
        self._steps = scenario.get("steps", [])
        self._timeout = scenario.get("timeout", 600.0)
        self._speed = scenario.get("speed")
        self._current_step_index = 0
        self._elapsed = 0.0
        self._running = False
        self._result = ScenarioResult(
            name=scenario.get("name", "Unnamed"),
            description=scenario.get("description", ""),
            steps=[
                StepResult(
                    step_index=i,
                    description=step.get("description", f"Step {i + 1}"),
                    status=StepStatus.PENDING,
                )
                for i, step in enumerate(self._steps)
            ],
        )

    def start(self) -> None:
        """シナリオの実行を開始する。"""
        if not self._scenario or not self._result:
            return
        setup = self._scenario.get("setup", {})
        self._apply_setup(setup)
        self._current_step_index = 0
        self._elapsed = 0.0
        self._running = True
        self._result.status = "running"
        self._result.elapsed_time = 0.0
        # ステップ結果を初期化
        for sr in self._result.steps:
            sr.status = StepStatus.PENDING
            sr.assertions = []
            sr.triggered_at = None

        # time=0 のステップを即座に処理
        self._process_pending_steps()

    def stop(self) -> None:
        """シナリオの実行を停止する。"""
        self._running = False
        if self._result:
            self._result.status = "stopped"

    def update(self, dt: float) -> None:
        """シミュレーションの各ステップで呼ばれる。"""
        if not self._running or not self._result:
            return

        self._elapsed += dt
        self._result.elapsed_time = round(self._elapsed, 1)

        # タイムアウト判定
        if self._elapsed > self._timeout:
            self._result.status = "timeout"
            for i in range(self._current_step_index, len(self._steps)):
                if self._result.steps[i].status in (
                    StepStatus.PENDING,
                    StepStatus.WAITING,
                ):
                    self._result.steps[i].status = StepStatus.SKIPPED
            self._running = False
            return

        self._process_pending_steps()

    def _process_pending_steps(self) -> None:
        """トリガー条件を満たしたステップを順に処理する。"""
        if not self._result:
            return

        while self._current_step_index < len(self._steps):
            step = self._steps[self._current_step_index]
            trigger = step.get("trigger", {})

            if not self._should_trigger(trigger):
                # 待機中ステータスに更新（wait 条件の場合）
                if trigger.get("type") == "wait":
                    self._result.steps[
                        self._current_step_index
                    ].status = StepStatus.WAITING
                break

            self._execute_step(self._current_step_index)
            self._current_step_index += 1

        # 全ステップ完了判定
        if self._current_step_index >= len(self._steps):
            self._finish()

    def _should_trigger(self, trigger: dict) -> bool:
        """トリガー条件を評価する。"""
        trigger_type = trigger.get("type", "time")

        if trigger_type == "time":
            return self._elapsed >= trigger.get("seconds", 0)

        if trigger_type == "wait":
            condition = trigger.get("condition", {})
            return self._evaluate_condition(condition)

        return False

    def _evaluate_condition(self, condition: dict) -> bool:
        """条件を現在の状態に対して評価する。"""
        field_name = condition.get("field", "")
        op = condition.get("op", "==")
        value = condition.get("value")
        actual = self._get_state_value(field_name)
        return self._compare(actual, op, value)

    def _get_state_value(self, field_path: str) -> Any:
        """状態辞書からフィールド値を取得する（ドット記法対応）。"""
        state = self._build_state()
        parts = field_path.split(".")
        current: Any = state
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return current

    def _build_state(self) -> dict:
        """現在の状態辞書を構築する。"""
        d = self.controller.display
        return {
            "state": self.controller.state.value,
            "temperature": round(self.pot.water_temperature, 1),
            "temperature_display": d.temperature_display,
            "water_volume_ml": round(self.pot.water_volume_ml, 1),
            "keep_warm_mode": self.controller.keep_warm_mode.value,
            "target_temperature": self.controller.target_temperature,
            "water_level_indicators": d.water_level_indicators,
            "boiling_lamp": d.boiling_lamp,
            "keep_warm_lamp": d.keep_warm_lamp,
            "lock_lamp": d.lock_lamp,
            "locked": self.controller.locked,
            "dispensing": self.controller.dispensing,
            "timer_display": d.timer_display,
            "timer_running": self.controller.timer.running,
            "buzzer_active": self.controller.buzzer.is_active,
            "lid_closed": self.pot.lid_sensor.read(),
            "heater_on": self.pot.heater.is_on,
            "heater_duty": round(self.pot.heater.duty_cycle, 1),
            "power_on": self.pot.heater_power_supply.is_on,
            "pump_on": self.pot.pump.is_on,
            "error": self.controller.error_detector.has_error,
            "high_temp_error": self.controller.error_detector.high_temp_error,
            "temp_not_rising_error": self.controller.error_detector.temp_not_rising_error,
            "app": {
                "temperature": round(
                    self.controller.current_temperature_reading, 1
                ),
                "lid_closed": self.controller._lid_input.is_active(),
                "heater_active": self.controller.is_heater_active,
                "heater_duty": round(self.controller._heater_duty.duty, 1),
                "pump_on": self.controller._pump_output.is_high,
                "power_on": self.controller._power_supply_output.is_high,
            },
            "plant": {
                "temperature": round(self.pot.water_temperature, 1),
                "lid_closed": self.pot.lid_sensor.read(),
                "heater_on": self.pot.heater.is_on,
                "heater_duty": round(self.pot.heater.duty_cycle, 1),
                "pump_on": self.pot.pump.is_on,
                "power_on": self.pot.heater_power_supply.is_on,
            },
        }

    @staticmethod
    def _compare(actual: Any, op: str, expected: Any) -> bool:
        """値を比較する。"""
        if actual is None:
            return False
        try:
            if op == "==":
                return actual == expected
            if op == "!=":
                return actual != expected
            if op == ">=":
                return float(actual) >= float(expected)
            if op == "<=":
                return float(actual) <= float(expected)
            if op == ">":
                return float(actual) > float(expected)
            if op == "<":
                return float(actual) < float(expected)
            if op == "in_range":
                lo, hi = expected
                return float(lo) <= float(actual) <= float(hi)
        except (TypeError, ValueError, IndexError):
            return False
        return False

    # -------------------------------------------------------------------
    # 初期セットアップ
    # -------------------------------------------------------------------

    def _apply_setup(self, setup: dict) -> None:
        """初期条件を適用する。"""
        if "water_ml" in setup:
            temp = setup.get("water_temperature", 25.0)
            self.pot.set_water(setup["water_ml"], temp)

        if "lid_closed" in setup:
            self.pot.lid_sensor.set_closed(setup["lid_closed"])

        # 状態を伝播
        self.pot.step(0)
        self.controller.update(0)

    # -------------------------------------------------------------------
    # アクション実行
    # -------------------------------------------------------------------

    def _execute_step(self, index: int) -> None:
        """ステップを実行する（アクション → 伝播 → アサーション）。"""
        if not self._result:
            return
        step = self._steps[index]
        step_result = self._result.steps[index]
        step_result.triggered_at = round(self._elapsed, 1)

        # アクション実行
        for action in step.get("actions", []):
            self._execute_action(action)

        # アサーション検証
        assertions = step.get("assertions", [])
        if assertions:
            self._check_assertions(assertions, step_result)
        else:
            step_result.status = StepStatus.PASSED

    def _execute_action(self, action: dict) -> None:
        """単一のアクションを実行する。"""
        action_type = action.get("type", "")

        if action_type == "add_water":
            self.pot.add_water(float(action.get("value", 0)))
        elif action_type == "set_water":
            vol = float(action.get("value", 0))
            temp = action.get("temperature")
            self.pot.set_water(vol, temp)
        elif action_type == "lid_open":
            self.pot.lid_sensor.set_closed(False)
        elif action_type == "lid_close":
            self.pot.lid_sensor.set_closed(True)
        elif action_type == "boil":
            self.controller.on_boil_button()
        elif action_type == "keep_warm":
            self.controller.on_keep_warm_button()
        elif action_type == "timer":
            self.controller.on_timer_button()
        elif action_type == "unlock":
            self.controller.on_unlock_button()
        elif action_type == "dispense_press":
            self.controller.on_dispense_press()
        elif action_type == "dispense_release":
            self.controller.on_dispense_release()
        elif action_type == "inject_fault":
            self._inject_fault(action)
        elif action_type == "clear_all_faults":
            self._clear_all_faults()

        # アクション後の状態伝播
        self.pot.step(0)
        self.controller.update(0)

    def _inject_fault(self, action: dict) -> None:
        """故障を注入する。"""
        target = action.get("target", "")
        mode_str = action.get("mode", "")

        sensor_fault_modes = {m.value: m for m in FaultMode}
        actuator_fault_modes = {m.value: m for m in ActuatorFaultMode}
        power_supply_fault_modes = {m.value: m for m in PowerSupplyFaultMode}

        sensor_map = {
            "thermistor": self.pot.thermistor,
            "water_level_1": self.pot.water_level_sensors[0],
            "water_level_2": self.pot.water_level_sensors[1],
            "water_level_3": self.pot.water_level_sensors[2],
            "water_level_4": self.pot.water_level_sensors[3],
            "full_water": self.pot.full_water_sensor,
            "lid_sensor": self.pot.lid_sensor,
        }
        if target in sensor_map:
            mode = sensor_fault_modes.get(mode_str)
            if mode:
                sensor_map[target].inject_fault(mode)
            return

        actuator_map = {"heater": self.pot.heater, "pump": self.pot.pump}
        if target in actuator_map:
            mode = actuator_fault_modes.get(mode_str)
            if mode:
                actuator_map[target].inject_fault(mode)
            return

        if target == "power_supply":
            mode = power_supply_fault_modes.get(mode_str)
            if mode:
                self.pot.heater_power_supply.inject_fault(mode)

    def _clear_all_faults(self) -> None:
        """全故障をクリアする。"""
        self.pot.thermistor.clear_fault()
        for s in self.pot.water_level_sensors:
            s.clear_fault()
        self.pot.full_water_sensor.clear_fault()
        self.pot.lid_sensor.clear_fault()
        self.pot.heater.clear_fault()
        self.pot.pump.clear_fault()
        self.pot.heater_power_supply.clear_fault()

    # -------------------------------------------------------------------
    # アサーション検証
    # -------------------------------------------------------------------

    def _check_assertions(
        self, assertions: list[dict], step_result: StepResult
    ) -> None:
        """アサーションを検証する。"""
        all_passed = True
        for assertion in assertions:
            field_name = assertion.get("field", "")
            op = assertion.get("op", "==")
            expected = assertion.get("expected")
            actual = self._get_state_value(field_name)
            passed = self._compare(actual, op, expected)

            step_result.assertions.append(
                AssertionResult(
                    field=field_name,
                    op=op,
                    expected=expected,
                    actual=actual,
                    passed=passed,
                    message=(
                        ""
                        if passed
                        else f"{field_name}: expected {op} {expected}, got {actual}"
                    ),
                )
            )
            if not passed:
                all_passed = False

        step_result.status = StepStatus.PASSED if all_passed else StepStatus.FAILED

    # -------------------------------------------------------------------
    # 完了処理
    # -------------------------------------------------------------------

    def _finish(self) -> None:
        """シナリオの実行を完了する。"""
        self._running = False
        if not self._result:
            return
        any_failed = any(
            s.status == StepStatus.FAILED for s in self._result.steps
        )
        self._result.status = "failed" if any_failed else "passed"

    # -------------------------------------------------------------------
    # 結果のシリアライズ
    # -------------------------------------------------------------------

    def get_result_dict(self) -> dict | None:
        """結果を辞書として返す。"""
        if not self._result:
            return None

        return {
            "name": self._result.name,
            "description": self._result.description,
            "status": self._result.status,
            "elapsed_time": self._result.elapsed_time,
            "steps": [
                {
                    "index": sr.step_index,
                    "description": sr.description,
                    "status": sr.status.value,
                    "triggered_at": sr.triggered_at,
                    "assertions": [
                        {
                            "field": ar.field,
                            "op": ar.op,
                            "expected": ar.expected,
                            "actual": ar.actual,
                            "passed": ar.passed,
                            "message": ar.message,
                        }
                        for ar in sr.assertions
                    ],
                }
                for sr in self._result.steps
            ],
        }

    # -------------------------------------------------------------------
    # 同期実行（REST API 用）
    # -------------------------------------------------------------------

    @staticmethod
    def run_scenario_sync(
        scenario: dict,
        cancel_event: Event | None = None,
        deadline_seconds: float | None = None,
    ) -> dict:
        """シナリオを同期的に最後まで実行し結果を返す。

        独立した Pot / Controller インスタンスを生成して実行するため、
        既存のシミュレーション状態に影響を与えない。

        Args:
            scenario: テストシナリオ定義辞書
            cancel_event: セットされると次のシミュレーションtickで停止するイベント。
            deadline_seconds: 実時間の実行上限。超過時は ``error`` 結果を返す。

        Returns:
            テスト結果辞書
        """
        pot = Pot()
        controller = PotController(pot)
        runner = ScenarioRunner(pot, controller)
        runner.load(scenario)
        runner.start()

        dt = 1.0
        max_steps = int(runner._timeout / dt) + 1
        deadline = monotonic() + deadline_seconds if deadline_seconds is not None else None
        for _ in range(max_steps):
            if not runner.running:
                break
            if cancel_event is not None and cancel_event.is_set():
                runner.stop()
                break
            if deadline is not None and monotonic() >= deadline:
                runner.stop()
                result = runner.get_result_dict() or {}
                result["status"] = "error"
                result["error_code"] = "EXECUTION_TIMEOUT"
                return result
            pot.step(dt)
            controller.update(dt)
            runner.update(dt)

        return runner.get_result_dict() or {
            "name": scenario.get("name", "Unknown"),
            "description": scenario.get("description", ""),
            "status": "error",
            "elapsed_time": 0.0,
            "steps": [],
        }


# ---------------------------------------------------------------------------
# 複数テストケース管理
# ---------------------------------------------------------------------------


def normalize_suite(data: dict) -> list[dict]:
    """ファイルJSONを正規化し、シナリオ配列を返す。

    新形式 (``{"scenarios": [...]}``): そのまま配列を返す。
    旧形式 (``{"name": ..., "steps": [...]}``) : 1要素の配列に包んで返す。
    """
    if "scenarios" in data and isinstance(data["scenarios"], list):
        return data["scenarios"]
    # 旧形式: トップレベルに steps があれば単一シナリオとみなす
    if "steps" in data:
        return [data]
    return []


class ScenarioSuiteRunner:
    """テストケースファイル内の複数シナリオを管理・順次実行するランナー。

    内部で :class:`ScenarioRunner` を利用し、1シナリオずつ実行する。
    """

    def __init__(self, pot: Pot, controller: PotController) -> None:
        self.pot = pot
        self.controller = controller
        self._scenarios: list[dict] = []
        self._results: list[dict | None] = []
        self._current_index: int = -1
        self._runner: ScenarioRunner = ScenarioRunner(pot, controller)
        self._run_all_mode: bool = False
        self._suite_status: str = "idle"

    # -- プロパティ --------------------------------------------------------

    @property
    def scenarios(self) -> list[dict]:
        return self._scenarios

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def running(self) -> bool:
        return self._runner.running

    @property
    def suite_status(self) -> str:
        return self._suite_status

    @property
    def preferred_speed(self) -> float | None:
        if 0 <= self._current_index < len(self._scenarios):
            return self._scenarios[self._current_index].get("speed")
        return None

    # -- ロード・選択 ------------------------------------------------------

    def load(self, data: dict) -> None:
        """ファイルJSON (新/旧形式) をロードする。"""
        self._scenarios = normalize_suite(data)
        self._results = [None] * len(self._scenarios)
        self._current_index = 0 if self._scenarios else -1
        self._run_all_mode = False
        self._suite_status = "loaded" if self._scenarios else "idle"

    def select(self, index: int) -> None:
        """テストケースを選択する。"""
        if 0 <= index < len(self._scenarios):
            self._current_index = index

    # -- 実行制御 ----------------------------------------------------------

    def start_current(self) -> None:
        """選択中のテストケースを実行開始する。

        Pot/Controller はリセット済みであること。
        """
        if self._current_index < 0 or self._current_index >= len(self._scenarios):
            return
        self._run_all_mode = False
        scenario = self._scenarios[self._current_index]
        self._runner.load(scenario)
        self._runner.start()
        self._suite_status = "running"

    def start_all(self) -> None:
        """全テストケースを先頭から順次実行する。"""
        if not self._scenarios:
            return
        self._run_all_mode = True
        self._results = [None] * len(self._scenarios)
        self._current_index = 0
        scenario = self._scenarios[0]
        self._runner.load(scenario)
        self._runner.start()
        self._suite_status = "running"

    def stop(self) -> None:
        """実行中のテストケースを停止する。"""
        self._runner.stop()
        self._run_all_mode = False
        self._suite_status = "stopped"

    def update(self, dt: float) -> bool:
        """シミュレーションステップ毎に呼ぶ。

        Returns:
            True のとき、現在のシナリオが完了して Pot/Controller リセットが必要。
        """
        if not self._runner.running:
            return False

        was_running = True
        self._runner.update(dt)

        if was_running and not self._runner.running:
            # 現在のシナリオが完了
            self._results[self._current_index] = self._runner.get_result_dict()

            if self._run_all_mode:
                next_idx = self._current_index + 1
                if next_idx < len(self._scenarios):
                    self._current_index = next_idx
                    # 呼び出し元でリセット→再呼出しが必要
                    return True
                else:
                    # 全件完了
                    self._run_all_mode = False
                    self._update_suite_status()
                    return False
            else:
                self._update_suite_status()

        return False

    def advance_after_reset(self) -> bool:
        """Pot/Controller リセット後に次のシナリオを開始する。

        ``update()`` が True を返した後に、
        Pot/Controller をリセットしてから呼ぶこと。

        Returns:
            True のとき、シナリオが start() 内で即座に完了したため
            Pot/Controller の再リセットが必要。
        """
        self._runner = ScenarioRunner(self.pot, self.controller)
        if 0 <= self._current_index < len(self._scenarios):
            scenario = self._scenarios[self._current_index]
            self._runner.load(scenario)
            self._runner.start()
            # 全ステップが seconds=0 で start() 内に即完了した場合
            if not self._runner.running:
                self._results[self._current_index] = self._runner.get_result_dict()
                if self._run_all_mode:
                    next_idx = self._current_index + 1
                    if next_idx < len(self._scenarios):
                        self._current_index = next_idx
                        return True  # 次のシナリオのために再リセットが必要
                    else:
                        self._run_all_mode = False
                        self._update_suite_status()
        return False

    # -- 結果 --------------------------------------------------------------

    def get_result_dict(self) -> dict:
        """スイート全体の結果辞書を返す。"""
        scenarios_list = []
        for i, sc in enumerate(self._scenarios):
            r = self._results[i]
            scenarios_list.append({
                "index": i,
                "name": sc.get("name", f"Scenario {i + 1}"),
                "description": sc.get("description", ""),
                "status": r["status"] if r else "pending",
                "elapsed_time": r["elapsed_time"] if r else 0.0,
            })
        current = self._runner.get_result_dict() if self._current_index >= 0 else None
        return {
            "suite_status": self._suite_status,
            "current_index": self._current_index,
            "scenarios": scenarios_list,
            "current": current,
        }

    # -- 内部 --------------------------------------------------------------

    def _update_suite_status(self) -> None:
        completed = [r for r in self._results if r is not None]
        if not completed:
            self._suite_status = "idle"
            return
        if any(r["status"] == "failed" for r in completed):
            self._suite_status = "failed"
        elif all(r is not None and r["status"] == "passed" for r in self._results):
            self._suite_status = "passed"
        else:
            self._suite_status = "done"

    # -- 同期実行 (REST API) -----------------------------------------------

    @staticmethod
    def run_suite_sync(
        data: dict,
        cancel_event: Event | None = None,
        deadline_seconds: float | None = None,
    ) -> dict:
        """複数テストケースを同期的に全件実行し結果を返す。"""
        scenarios = normalize_suite(data)
        results = []
        deadline = monotonic() + deadline_seconds if deadline_seconds is not None else None
        for sc in scenarios:
            remaining_seconds = None if deadline is None else max(0.0, deadline - monotonic())
            r = ScenarioRunner.run_scenario_sync(sc, cancel_event, remaining_seconds)
            results.append(r)
            if r.get("status") in {"stopped", "error"}:
                break
        any_failed = any(r["status"] == "failed" for r in results)
        all_passed = all(r["status"] == "passed" for r in results)
        return {
            "suite_status": "failed" if any_failed else ("passed" if all_passed else "done"),
            "scenarios": results,
        }
