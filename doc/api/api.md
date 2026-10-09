# テストシナリオ JSON 記述リファレンス

シミュレータに投入するテストシナリオは JSON ファイルで定義します。  
1 ファイルに複数のテストケースを格納でき、`tests/product/` に配置すると `tests/run_all.py` から自動検出されます。

C++ソース差分をMCPへ投入する手順は[MCP APIリファレンス](mcp.md)を参照してください。

## 1. ファイル構造

```json
{
  "scenarios": [
    { テストケース1 },
    { テストケース2 },
    ...
  ]
}
```

| キー | 型 | 必須 | 説明 |
|------|----|------|------|
| `scenarios` | array | ○ | テストケースの配列。各要素が 1 つのテストケースを表す。 |

> **後方互換**: トップレベルに `steps` キーがある旧形式（1 ファイル 1 テストケース）も自動的に `{"scenarios": [元データ]}` として解釈されます。

---

## 2. テストケース構造

`scenarios` 配列の各要素は以下の構造を持ちます。

```json
{
  "name":        "シナリオ名（必須）",
  "description": "説明文（任意）",
  "speed":       5,
  "timeout":     30,
  "setup":       { ... },
  "steps":       [ ... ]
}
```

| キー | 型 | 必須 | 説明 |
|------|----|------|------|
| `name` | string | ○ | シナリオの識別名。結果レポートに表示される。 |
| `description` | string | | 人間向けの補足説明。 |
| `speed` | number | | realtime実行時の推奨速度倍率（例: `5` = 実時間の5倍速）。instant REST/MCP実行では実時間待ちを行わない。 |
| `timeout` | number | | シナリオ全体のタイムアウト秒数（シミュレーション時間）。省略時 600 秒。超過したときは未実行ステップが `skipped` になる。 |
| `setup` | object | | シナリオ開始前の初期状態。省略時はシミュレータのデフォルト状態。 |
| `steps` | array | ○ | テストステップの配列。上から順に評価される。 |

---

## 3. `setup` オブジェクト

```json
"setup": {
  "water_ml":          600,
  "water_temperature": 25.0,
  "lid_closed":        false
}
```

| キー | 型 | 説明 |
|------|----|------|
| `water_ml` | number | 初期水量 (ml)。 |
| `water_temperature` | number | 初期水温 (℃)。省略時 25.0。 |
| `lid_closed` | boolean | 蓋の初期状態。`true` = 閉、`false` = 開。省略時 `false`。 |

---

## 4. ステップ構造

```json
{
  "description": "ステップの説明（任意）",
  "trigger":     { ... },
  "actions":     [ ... ],
  "assertions":  [ ... ]
}
```

各ステップは **トリガー → アクション実行 → アサーション検証** の順で処理されます。  
ステップは配列の先頭から順番に評価され、前のステップのトリガーが発火しない限り後続ステップは評価されません。

---

## 5. トリガー (`trigger`)

### 5.1 時刻トリガー

指定した経過時間（シミュレーション秒）に達したら発火します。

```json
"trigger": { "type": "time", "seconds": 1 }
```

| キー | 型 | 説明 |
|------|----|------|
| `type` | string | `"time"` 固定。 |
| `seconds` | number | 発火する経過時間（シミュレーション秒）。`0` は即時発火。 |

### 5.2 条件待機トリガー

指定した状態条件が満たされるまで毎フレーム再評価します。  
`timeout` を超えるとステップが `skipped` になります。

```json
"trigger": {
  "type": "wait",
  "condition": { "field": "state", "op": "==", "value": "keep_warm" }
}
```

| キー | 型 | 説明 |
|------|----|------|
| `type` | string | `"wait"` 固定。 |
| `condition.field` | string | 監視するフィールド名（[§7 状態フィールド一覧](#7-状態フィールド一覧) 参照）。 |
| `condition.op` | string | 比較演算子（[§8 アサーション演算子](#8-アサーション演算子) 参照）。 |
| `condition.value` | any | 期待値。 |

> **注意**: `time` トリガーと `wait` トリガーは混在できます。`wait` の直後に `"type": "time", "seconds": 0` を置くと、待機完了後に即時アクションを実行できます。

---

## 6. アクション (`actions`)

アクションは配列で複数指定でき、先頭から順に実行されます。

### 6.1 注水・水量操作

| `type` | 追加キー | 説明 |
|--------|----------|------|
| `add_water` | `value`: number (ml) | 現在の水量に加算する。 |
| `set_water` | `value`: number (ml), `temperature`: number (℃, 任意) | 水量を指定値に設定する。`temperature` を省略すると現在の水温を維持。 |

```json
{ "type": "add_water", "value": 600 }
{ "type": "set_water", "value": 300, "temperature": 60.0 }
```

### 6.2 蓋操作

| `type` | 説明 |
|--------|------|
| `lid_open` | 蓋を開ける。 |
| `lid_close` | 蓋を閉める。 |

### 6.3 ボタン操作

| `type` | 説明 |
|--------|------|
| `boil` | 沸騰ボタンを押す。 |
| `keep_warm` | 保温設定ボタンを押す（保温モードを1段階進める）。 |
| `timer` | タイマーボタンを押す。 |
| `unlock` | ロック解除ボタンを押す（給湯ロック切り替え）。 |

### 6.4 給湯操作

| `type` | 説明 |
|--------|------|
| `dispense_press` | 給湯ボタンを押す（押し続ける開始）。ポンプが ON になる。 |
| `dispense_release` | 給湯ボタンを離す。ポンプが OFF になる。 |

### 6.5 故障注入

```json
{ "type": "inject_fault", "target": "heater", "mode": "open" }
{ "type": "clear_all_faults" }
```

#### `inject_fault` のパラメータ

| `target` | 対象 | 使用可能な `mode` |
|----------|------|------------------|
| `thermistor` | サーミスタ（水温センサ） | `normal` / `open`（断線） / `short`（GND短絡） |
| `water_level_1` | 水位センサ 1 | 同上 |
| `water_level_2` | 水位センサ 2 | 同上 |
| `water_level_3` | 水位センサ 3 | 同上 |
| `water_level_4` | 水位センサ 4 | 同上 |
| `full_water` | 満水センサ | 同上 |
| `lid_sensor` | 蓋センサ | 同上 |
| `heater` | ヒータ | `normal` / `open`（断線、常に OFF） / `short_on`（固着 ON） |
| `pump` | ポンプ | 同上 |
| `power_supply` | ヒータ電源 | `normal` / `stuck_on`（固着 ON） / `stuck_off`（固着 OFF） |

#### `clear_all_faults`

すべての故障をクリアして正常状態に戻します。追加パラメータなし。

---

## 7. 状態フィールド一覧

`assertions[].field` および `trigger.condition.field` に指定できるフィールドです。  
ドット記法でネスト階層にアクセスできます（例: `app.temperature`）。

### 7.1 制御状態

| フィールド | 型 | 説明 |
|----------|-----|------|
| `state` | string | 制御状態。値: `"idle"` / `"boiling"` / `"keep_warm"` / `"error"` |
| `keep_warm_mode` | string | 保温モード。値: `"high"`（98℃）/ `"economy"`（90℃）/ `"milk"`（60℃） |
| `target_temperature` | number | 現在の目標温度 (℃)。 |
| `locked` | boolean | 給湯ロック状態。`true` = ロック中。 |
| `dispensing` | boolean | 給湯中フラグ。 |
| `timer_running` | boolean | タイマー動作中フラグ。 |
| `timer_display` | string | タイマー表示文字列。 |

### 7.2 センサ・測定値

| フィールド | 型 | 説明 |
|----------|-----|------|
| `temperature` | number | 水温 (℃)。小数第 1 位まで。 |
| `temperature_display` | string | ディスプレイ表示温度文字列。 |
| `water_volume_ml` | number | 水量 (ml)。小数第 1 位まで。 |
| `water_level_indicators` | array | 水位インジケータ状態（配列）。 |
| `lid_closed` | boolean | 蓋センサ値。`true` = 閉。 |

### 7.3 アクチュエータ・出力

| フィールド | 型 | 説明 |
|----------|-----|------|
| `heater_on` | boolean | ヒータ ON 状態。 |
| `heater_duty` | number | ヒータデューティ比 (%)。小数第 1 位まで。 |
| `pump_on` | boolean | ポンプ ON 状態。 |
| `power_on` | boolean | ヒータ電源 ON 状態。 |
| `buzzer_active` | boolean | ブザー鳴動中フラグ。 |

### 7.4 UI ランプ

| フィールド | 型 | 説明 |
|----------|-----|------|
| `boiling_lamp` | boolean | 沸騰ランプ点灯状態。 |
| `keep_warm_lamp` | boolean | 保温ランプ点灯状態。 |
| `lock_lamp` | boolean | ロックランプ点灯状態。 |

### 7.5 エラー検知

| フィールド | 型 | 説明 |
|----------|-----|------|
| `error` | boolean | いずれかのエラーが発生中。 |
| `high_temp_error` | boolean | 過温度エラー発生中。 |
| `temp_not_rising_error` | boolean | 温度上昇なしエラー発生中（ヒータ断線検知など）。 |

### 7.6 ネスト参照（デバッグ用）

`app.*` はコントローラ側から見た値、`plant.*` はプラントモデルの実測値を表します。

| フィールド | 説明 |
|----------|------|
| `app.temperature` | コントローラが読んでいる温度 (℃)。 |
| `app.lid_closed` | コントローラが読んでいる蓋センサ値。 |
| `app.heater_active` | コントローラがヒータをアクティブと判断しているか。 |
| `app.heater_duty` | コントローラが指令しているデューティ比 (%)。 |
| `app.pump_on` | コントローラが指令しているポンプ状態。 |
| `app.power_on` | コントローラが指令している電源状態。 |
| `plant.temperature` | プラントモデルの実際の水温 (℃)。 |
| `plant.lid_closed` | プラントモデルの実際の蓋センサ値。 |
| `plant.heater_on` | プラントモデルのヒータ実動作状態。 |
| `plant.heater_duty` | プラントモデルのヒータデューティ比 (%)。 |
| `plant.pump_on` | プラントモデルのポンプ実動作状態。 |
| `plant.power_on` | プラントモデルの電源実動作状態。 |

---

## 8. アサーション演算子

`assertions[].op` および `trigger.condition.op` に使用します。

| 演算子 | 意味 | `expected` の型 |
|--------|------|-----------------|
| `==` | 等しい | any |
| `!=` | 等しくない | any |
| `>` | より大きい | number |
| `<` | より小さい | number |
| `>=` | 以上 | number |
| `<=` | 以下 | number |
| `in_range` | 範囲内 (両端含む) | `[min, max]` の配列 |

```json
{ "field": "temperature", "op": "in_range", "expected": [95.0, 100.0] }
```

---

## 9. 実行結果

各ステップの結果は以下のいずれかになります。

| ステータス | 説明 |
|----------|------|
| `passed` | すべてのアサーションが通過。 |
| `failed` | 1 つ以上のアサーションが失敗。 |
| `skipped` | タイムアウトにより未実行。 |
| `waiting` | `wait` トリガーの条件待ち中。 |
| `pending` | まだ評価されていない。 |

シナリオ全体のステータスは `passed` / `failed` / `timeout` / `stopped` のいずれかです。

---

## 10. 完全な記述例

```json
{
  "scenarios": [
    {
      "name": "S-1.1: 温度制御可能水位で蓋を閉じる → 沸騰開始",
      "description": "水を600ml注ぎ、蓋を閉じると沸騰が開始されることを確認する",
      "speed": 5,
      "timeout": 30,
      "setup": {
        "water_ml": 0,
        "water_temperature": 25.0,
        "lid_closed": false
      },
      "steps": [
        {
          "description": "水を600ml注ぐ",
          "trigger": { "type": "time", "seconds": 0 },
          "actions": [
            { "type": "add_water", "value": 600 }
          ],
          "assertions": [
            { "field": "water_volume_ml", "op": ">=", "expected": 600 },
            { "field": "state", "op": "==", "expected": "idle" }
          ]
        },
        {
          "description": "蓋を閉じる → 沸騰開始を確認",
          "trigger": { "type": "time", "seconds": 1 },
          "actions": [
            { "type": "lid_close" }
          ],
          "assertions": [
            { "field": "state", "op": "==", "expected": "boiling" },
            { "field": "boiling_lamp", "op": "==", "expected": true },
            { "field": "keep_warm_lamp", "op": "==", "expected": false },
            { "field": "heater_on", "op": "==", "expected": true }
          ]
        }
      ]
    }
  ]
}
```
