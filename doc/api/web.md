# Web APIリファレンス

Webサーバーは既定で`http://127.0.0.1:8000`を使用します。

```bash
uv run python -m web.app
```

## REST API

### `GET /api/scenarios`

`tests/product/*.json`にあるシナリオファイルの一覧を返します。

```json
[
  {
    "file": "sample_tests.json",
    "name": "sample_tests",
    "count": 7,
    "scenarios": [
      {
        "name": "シナリオ名",
        "description": "シナリオの説明"
      }
    ]
  }
]
```

### `POST /api/scenarios/run`

リクエストボディのテストケースJSONを実行します。

```bash
curl -X POST "http://127.0.0.1:8000/api/scenarios/run?mode=instant" \
  -H "Content-Type: application/json" \
  -d @tests/product/sample_tests.json
```

### `POST /api/scenarios/run/{filename}`

`tests/product/`内のファイルを指定して実行します。パスではなくファイル名を指定します。

```bash
curl -X POST "http://127.0.0.1:8000/api/scenarios/run/sample_tests.json"
```

両方のPOST endpointで次のquery parameterを利用できます。

| パラメーター | 既定値 | 説明 |
|---|---:|---|
| `mode` | `instant` | `instant`は共有UIと独立して即時実行、`realtime`は共有シミュレーション上で実行 |
| `speed` | シナリオの`speed`または`5.0` | realtime時の速度。`0.5`から`20.0` |

レスポンスはスイート結果です。`scenarios`内に各シナリオの`status`、`elapsed_time`、
ステップとアサーション結果が含まれます。テストケースJSONの仕様は
[テストケースJSON記述リファレンス](api.md)を参照してください。

## WebSocket

接続先は`ws://127.0.0.1:8000/ws`です。接続直後と各操作後、およびシミュレーション実行中に
次の形式で状態が配信されます。

```json
{
  "type": "state",
  "data": {
    "state": "idle",
    "temperature": 25.0,
    "water_volume_ml": 0.0,
    "running": false,
    "speed": 1.0,
    "temp_history": []
  }
}
```

クライアントからは`action`を含むJSONを送信します。

| `action` | 追加フィールド | 説明 |
|---|---|---|
| `start`, `stop`, `reset`, `step` | なし | シミュレーション制御 |
| `set_speed` | `value` | 速度を`0.5`から`20.0`に設定 |
| `add_water` | `value` | `0`より大きく`1000`以下の水量を追加 |
| `lid_open`, `lid_close` | なし | 蓋操作 |
| `boil`, `keep_warm`, `timer`, `unlock` | なし | ボタン操作 |
| `dispense_press`, `dispense_release` | なし | 給湯ボタン操作 |
| `inject_fault` | `target`, `mode` | 故障注入 |
| `clear_all_faults` | なし | 全故障を解除 |
| `load_scenario` | `scenario` | JSONからスイートを読み込む |
| `load_scenario_file` | `name` | `tests/product/`のファイルを読み込む |
| `select_scenario` | `index` | 実行対象を選択 |
| `run_scenario`, `run_all_scenarios`, `stop_scenario` | なし | シナリオ実行制御 |

例:

```json
{"action": "set_speed", "value": 5}
```

```json
{"action": "inject_fault", "target": "heater", "mode": "open"}
```

WebSocketは単一の共有シミュレーションを操作します。複数クライアントを分離するセッション機構や
認証はありません。公開ネットワークへ直接公開しないでください。