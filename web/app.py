"""話題沸騰ポット シミュレーション Web UI サーバー。

FastAPI + WebSocket によるリアルタイムシミュレーション表示・操作。
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sim.plant.pot import Pot
from sim.plant.sensors.sensor import FaultMode
from sim.plant.actuators.actuator import ActuatorFaultMode
from sim.plant.devices.power_supply import PowerSupplyFaultMode
from sim.bridge import PotController, PotState, KeepWarmMode
from sim.scenario_runner import ScenarioRunner, ScenarioSuiteRunner, normalize_suite

# ---------------------------------------------------------------------------
# シミュレーション管理
# ---------------------------------------------------------------------------

STATIC_DIR = pathlib.Path(__file__).parent / "static"
SCENARIO_DIR = pathlib.Path(__file__).parent.parent / "tests" / "product"


class Simulation:
    """シミュレーション状態を管理するクラス。"""

    def __init__(self) -> None:
        self.reset()
        self.running: bool = False
        self.speed: float = 1.0  # 倍速 (1x, 2x, 5x, 10x)
        self._connections: list[WebSocket] = []
        self._task: asyncio.Task | None = None
        self.scenario_done: asyncio.Event = asyncio.Event()

    def reset(self) -> None:
        self.pot = Pot()
        self.controller = PotController(self.pot)
        self.elapsed_time: float = 0.0
        self.temp_history: list[dict] = []
        self.suite_runner = ScenarioSuiteRunner(self.pot, self.controller)

    def get_state(self) -> dict:
        """現在のシミュレーション状態を辞書で返す。"""
        d = self.controller.display
        suite_result = self.suite_runner.get_result_dict()
        return {
            "elapsed_time": round(self.elapsed_time, 1),
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
            "heater_duty": self.pot.heater.duty_cycle,
            "power_on": self.pot.heater_power_supply.is_on,
            "pump_on": self.pot.pump.is_on,
            "error": self.controller.error_detector.has_error,
            "high_temp_error": self.controller.error_detector.high_temp_error,
            "temp_not_rising_error": self.controller.error_detector.temp_not_rising_error,
            "running": self.running,
            "speed": self.speed,
            # アプリ認識値 (電気信号経由)
            "app": {
                "temperature": round(self.controller.current_temperature_reading, 1),
                "temperature_display": d.temperature_display,
                "water_level": self.controller.water_level_readings,
                "lid_closed": self.controller._lid_input.is_active(),
                "heater_active": self.controller.is_heater_active,
                "heater_duty": round(self.controller._heater_duty.duty, 1),
                "pump_on": self.controller._pump_output.is_high,
                "power_on": self.controller._power_supply_output.is_high,
            },
            # プラント実値
            "plant": {
                "temperature": round(self.pot.water_temperature, 1),
                "water_level": [s.read() for s in self.pot.water_level_sensors],
                "lid_closed": self.pot.lid_sensor.read(),
                "heater_on": self.pot.heater.is_on,
                "heater_duty": round(self.pot.heater.duty_cycle, 1),
                "pump_on": self.pot.pump.is_on,
                "power_on": self.pot.heater_power_supply.is_on,
            },
            # 故障状態
            "faults": self._get_fault_state(),
            # シナリオテスト結果
            "scenario": suite_result,
        }

    def _get_fault_state(self) -> dict:
        """全コンポーネントの故障状態を辞書で返す。"""
        return {
            "thermistor": self.pot.thermistor.fault_mode.value,
            "water_level_1": self.pot.water_level_sensors[0].fault_mode.value,
            "water_level_2": self.pot.water_level_sensors[1].fault_mode.value,
            "water_level_3": self.pot.water_level_sensors[2].fault_mode.value,
            "water_level_4": self.pot.water_level_sensors[3].fault_mode.value,
            "full_water": self.pot.full_water_sensor.fault_mode.value,
            "lid_sensor": self.pot.lid_sensor.fault_mode.value,
            "heater": self.pot.heater.fault_mode.value,
            "pump": self.pot.pump.fault_mode.value,
            "power_supply": self.pot.heater_power_supply.fault_mode.value,
        }

    def step(self, dt: float = 1.0) -> None:
        """シミュレーションを1ステップ進める。"""
        self.pot.step(dt)
        self.controller.update(dt)
        self.elapsed_time += dt
        # シナリオスイートランナー更新
        was_running = self.suite_runner.running
        if was_running:
            needs_reset = self.suite_runner.update(dt)
            if needs_reset:
                # 全件実行中: Pot/Controller をリセットして次のシナリオへ
                # start() で即座に完了するシナリオが連続する場合はループで処理する
                while True:
                    self.pot = Pot()
                    self.controller = PotController(self.pot)
                    self.suite_runner.pot = self.pot
                    self.suite_runner.controller = self.controller
                    if not self.suite_runner.advance_after_reset():
                        break
        # シナリオ完了を通知
        if was_running and not self.suite_runner.running:
            self.scenario_done.set()
        # 温度履歴を記録（最大600データポイント = 10分分）
        self.temp_history.append({
            "t": round(self.elapsed_time, 1),
            "temp": round(self.pot.water_temperature, 1),
            "app_temp": round(self.controller.current_temperature_reading, 1),
            "target": self.controller.target_temperature,
        })
        if len(self.temp_history) > 600:
            self.temp_history.pop(0)

    async def broadcast(self, data: dict) -> None:
        """全WebSocket接続にデータを送信する。"""
        dead: list[WebSocket] = []
        message = json.dumps(data, ensure_ascii=False)
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)

    def add_connection(self, ws: WebSocket) -> None:
        self._connections.append(ws)

    def remove_connection(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)

    async def run_loop(self) -> None:
        """バックグラウンドでシミュレーションを実行するループ。"""
        dt = 1.0
        while True:
            if self.running:
                self.step(dt)
                state = self.get_state()
                state["temp_history"] = self.temp_history[-60:]
                await self.broadcast({"type": "state", "data": state})
            # speed に応じて更新間隔を調整
            await asyncio.sleep(dt / self.speed)


sim = Simulation()


@asynccontextmanager
async def lifespan(app: FastAPI):
    sim._task = asyncio.create_task(sim.run_loop())
    yield
    if sim._task:
        sim._task.cancel()


# ---------------------------------------------------------------------------
# FastAPI アプリケーション
# ---------------------------------------------------------------------------

app = FastAPI(title="話題沸騰ポット シミュレーター", lifespan=lifespan)


@app.get("/api/scenarios")
async def list_scenarios():
    """利用可能なテストシナリオ一覧を返す。"""
    if not SCENARIO_DIR.exists():
        return []
    files = sorted(SCENARIO_DIR.glob("*.json"))
    result = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            scenarios = normalize_suite(data)
            result.append({
                "file": f.name,
                "name": f.stem,
                "count": len(scenarios),
                "scenarios": [
                    {"name": s.get("name", f"Scenario {i+1}"), "description": s.get("description", "")}
                    for i, s in enumerate(scenarios)
                ],
            })
        except (json.JSONDecodeError, OSError):
            result.append({"file": f.name, "name": f.stem, "count": 0, "scenarios": []})
    return result


@app.post("/api/scenarios/run")
async def run_scenario_from_body(
    scenario: dict,
    mode: str = Query("instant", pattern="^(instant|realtime)$"),
    speed: Optional[float] = Query(None, ge=0.5, le=20.0),
) -> dict:
    """リクエストボディのシナリオJSONを実行し結果を返す。

    Query params:
        mode: "instant" (即時実行, デフォルト) / "realtime" (UI連動リアルタイム実行)
        speed: realtime時の倍速 (0.5〜20, 省略時はシナリオの speed またはデフォルト5)
    """
    if mode == "realtime":
        return await _run_scenario_realtime(scenario, speed)
    return ScenarioSuiteRunner.run_suite_sync(scenario)


@app.post("/api/scenarios/run/{filename}")
async def run_scenario_from_file(
    filename: str,
    mode: str = Query("instant", pattern="^(instant|realtime)$"),
    speed: Optional[float] = Query(None, ge=0.5, le=20.0),
) -> dict:
    """tests/product 内のJSONファイル名を指定してシナリオを実行し結果を返す。

    Query params:
        mode: "instant" (即時実行, デフォルト) / "realtime" (UI連動リアルタイム実行)
        speed: realtime時の倍速 (0.5〜20, 省略時はシナリオの speed またはデフォルト5)
    """
    filepath = SCENARIO_DIR / filename
    if not filepath.exists() or filepath.suffix != ".json":
        return {"error": f"Scenario file not found: {filename}"}
    data = json.loads(filepath.read_text(encoding="utf-8"))
    if mode == "realtime":
        return await _run_scenario_realtime(data, speed)
    return ScenarioSuiteRunner.run_suite_sync(data)


async def _run_scenario_realtime(file_data: dict, speed: Optional[float]) -> dict:
    """共有シミュレーション上でスイートをリアルタイム実行し、完了を待って結果を返す。"""
    sim.running = False
    sim.reset()
    sim.suite_runner.load(file_data)

    # 速度決定: 引数 > シナリオ定義 > デフォルト5x
    effective_speed = speed or sim.suite_runner.preferred_speed or 5.0
    sim.speed = max(0.5, min(effective_speed, 20.0))

    sim.scenario_done.clear()
    sim.suite_runner.start_all()

    # start() 内で全ステップが即座に完了した場合
    if not sim.suite_runner.running:
        sim.running = False
        return sim.suite_runner.get_result_dict()

    sim.running = True

    # タイムアウト = 全シナリオの合計タイムアウト秒 / 倍速 + 余裕
    scenarios = normalize_suite(file_data)
    total_timeout = sum(s.get("timeout", 600) for s in scenarios)
    timeout_real = total_timeout / sim.speed + 10.0
    try:
        await asyncio.wait_for(sim.scenario_done.wait(), timeout=timeout_real)
    except asyncio.TimeoutError:
        sim.suite_runner.stop()

    sim.running = False
    return sim.suite_runner.get_result_dict()


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    sim.add_connection(ws)

    # 接続時に現在の状態を送信
    state = sim.get_state()
    state["temp_history"] = sim.temp_history[-60:]
    await ws.send_text(json.dumps({"type": "state", "data": state}, ensure_ascii=False))

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")

            if action == "start":
                sim.running = True
            elif action == "stop":
                sim.running = False
            elif action == "reset":
                sim.running = False
                sim.reset()
            elif action == "set_speed":
                speed = float(msg.get("value", 1.0))
                sim.speed = max(0.5, min(speed, 20.0))
            elif action == "step":
                # 手動で1ステップ進める
                sim.step(1.0)
            elif action == "add_water":
                volume = float(msg.get("value", 100.0))
                if 0 < volume <= 1000:
                    sim.pot.add_water(volume)
            elif action == "lid_open":
                sim.pot.lid_sensor.set_closed(False)
            elif action == "lid_close":
                sim.pot.lid_sensor.set_closed(True)
            elif action == "boil":
                sim.controller.on_boil_button()
            elif action == "keep_warm":
                sim.controller.on_keep_warm_button()
            elif action == "timer":
                sim.controller.on_timer_button()
            elif action == "unlock":
                sim.controller.on_unlock_button()
            elif action == "dispense_press":
                sim.controller.on_dispense_press()
            elif action == "dispense_release":
                sim.controller.on_dispense_release()
            elif action == "inject_fault":
                _handle_fault_injection(msg)
                sim.controller.update(0)  # 故障注入後、即座にアプリ状態を更新
            elif action == "clear_all_faults":
                _clear_all_faults()
                sim.controller.update(0)  # 故障解除後、即座にアプリ状態を更新
            elif action == "load_scenario":
                scenario_data = msg.get("scenario")
                if scenario_data:
                    sim.running = False
                    sim.reset()
                    sim.suite_runner.load(scenario_data)
            elif action == "load_scenario_file":
                filename = msg.get("name", "")
                filepath = SCENARIO_DIR / filename
                if filepath.exists() and filepath.suffix == ".json":
                    data = json.loads(filepath.read_text(encoding="utf-8"))
                    sim.running = False
                    sim.reset()
                    sim.suite_runner.load(data)
            elif action == "select_scenario":
                index = int(msg.get("index", 0))
                sim.suite_runner.select(index)
            elif action == "run_scenario":
                # 選択中のテストケースを実行
                saved_scenarios = sim.suite_runner.scenarios
                current_idx = sim.suite_runner.current_index
                sim.reset()
                if saved_scenarios:
                    sim.suite_runner._scenarios = saved_scenarios
                    sim.suite_runner._results = [None] * len(saved_scenarios)
                    sim.suite_runner._current_index = current_idx
                    sim.suite_runner._suite_status = "running"
                    speed = sim.suite_runner.preferred_speed
                    if speed:
                        sim.speed = max(0.5, min(speed, 20.0))
                    sim.suite_runner.start_current()
                    sim.running = True
            elif action == "run_all_scenarios":
                saved_scenarios = sim.suite_runner.scenarios
                sim.reset()
                if saved_scenarios:
                    sim.suite_runner._scenarios = saved_scenarios
                    sim.suite_runner._results = [None] * len(saved_scenarios)
                    sim.suite_runner._current_index = 0
                    speed = sim.suite_runner.preferred_speed
                    if speed:
                        sim.speed = max(0.5, min(speed, 20.0))
                    sim.suite_runner.start_all()
                    sim.running = True
            elif action == "stop_scenario":
                sim.suite_runner.stop()

            # アクション後の即座の状態更新
            state = sim.get_state()
            state["temp_history"] = sim.temp_history[-60:]
            await sim.broadcast({"type": "state", "data": state})

    except WebSocketDisconnect:
        sim.remove_connection(ws)
    except Exception:
        sim.remove_connection(ws)


# ---------------------------------------------------------------------------
# 故障注入ハンドラ
# ---------------------------------------------------------------------------

# センサ名と対象オブジェクトのマッピングを返す
def _get_sensor_map() -> dict:
    return {
        "thermistor": sim.pot.thermistor,
        "water_level_1": sim.pot.water_level_sensors[0],
        "water_level_2": sim.pot.water_level_sensors[1],
        "water_level_3": sim.pot.water_level_sensors[2],
        "water_level_4": sim.pot.water_level_sensors[3],
        "full_water": sim.pot.full_water_sensor,
        "lid_sensor": sim.pot.lid_sensor,
    }


# 故障モード文字列からEnumへの変換テーブル
_SENSOR_FAULT_MODES = {m.value: m for m in FaultMode}
_ACTUATOR_FAULT_MODES = {m.value: m for m in ActuatorFaultMode}
_POWER_SUPPLY_FAULT_MODES = {m.value: m for m in PowerSupplyFaultMode}


def _handle_fault_injection(msg: dict) -> None:
    """故障注入メッセージを処理する。

    メッセージ形式: {"action": "inject_fault", "target": "...", "mode": "..."}
    """
    target = msg.get("target", "")
    mode_str = msg.get("mode", "normal")

    # センサの故障
    sensor_map = _get_sensor_map()
    if target in sensor_map:
        mode = _SENSOR_FAULT_MODES.get(mode_str)
        if mode is not None:
            sensor_map[target].inject_fault(mode)
        return

    # アクチュエータの故障
    actuator_map = {
        "heater": sim.pot.heater,
        "pump": sim.pot.pump,
    }
    if target in actuator_map:
        mode = _ACTUATOR_FAULT_MODES.get(mode_str)
        if mode is not None:
            actuator_map[target].inject_fault(mode)
        return

    # 電源の故障
    if target == "power_supply":
        mode = _POWER_SUPPLY_FAULT_MODES.get(mode_str)
        if mode is not None:
            sim.pot.heater_power_supply.inject_fault(mode)


def _clear_all_faults() -> None:
    """全コンポーネントの故障をクリアする。"""
    sim.pot.thermistor.clear_fault()
    for s in sim.pot.water_level_sensors:
        s.clear_fault()
    sim.pot.full_water_sensor.clear_fault()
    sim.pot.lid_sensor.clear_fault()
    sim.pot.heater.clear_fault()
    sim.pot.pump.clear_fault()
    sim.pot.heater_power_supply.clear_fault()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
