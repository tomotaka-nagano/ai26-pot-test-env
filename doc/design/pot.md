# 話題沸騰ポット（GOMA-1015型）システム設計

## 1. 全体構成

本システムは、Python製プラントモデルと外部C++ baselineの制御アプリケーションを
pybind11 bridgeで接続します。WebサーバーとMCPサーバーは同じシミュレーション実行系を
異なる用途で公開します。

```text
外部baseline
  CMakeLists.txt + src_cpp/
          |
          | CMake build / pybind11
          v
pot_cpp Python拡張
          |
          v
sim/bridge.py <--> sim/plant/
       |               |
       +-------+-------+
               v
      sim/scenario_runner.py
          |             |
          v             v
      web/app.py    mcp_server/
```

| コンポーネント | 責務 |
|---|---|
| `sim/plant/` | 水温、水量、蒸発、センサ、アクチュエータ、故障を含む物理モデル |
| `sim/native_loader.py` | 外部baselineの検証、digest計算、オンデマンドビルド、`pot_cpp`のロード |
| `sim/bridge.py` | Pythonの電気I/OとC++インターフェースを接続し、互換APIを提供 |
| `sim/test_case_models.py` | テストケースのstrict schemaと許可値 |
| `sim/test_case_validator.py` | 外部入力の検証と正規化 |
| `sim/scenario_runner.py` | シナリオとスイートの同期・逐次実行、結果生成 |
| `web/app.py` | 単一の共有リアルタイムシミュレーション、REST API、WebSocket、Web UI |
| `mcp_server/` | FIFOジョブ、SQLite永続化、source set転送、分離processでのビルド・実行 |

## 2. 外部C++ baseline

制御アプリケーションのソースはこのリポジトリに含めず、`POT_MCP_BASE_SOURCE_DIR`が示す
外部プロジェクトを正とします。既定値は`../ai26-pot-application`です。baselineには
`CMakeLists.txt`と`src_cpp/`が必要です。

`sim/native_loader.py`はbaselineの対象ファイルからSHA-256 digestを計算し、CMakeで
`pot_cpp` Python拡張をビルドします。cache keyにはソースdigest、OS、Python、拡張suffixを
含めます。Web UIやローカルシナリオ実行の既定cacheは`.data/native/artifacts/`です。

MCP経由のsource setはbaselineを作業領域へ複製し、アップロードされたファイルの置換と削除を
適用してから同じbuild executorでビルドします。評価側のPythonプラント、bridge、runnerは
source setでは変更できません。

## 3. プラントと制御の境界

C++側は具体的なPythonオブジェクトではなく、pybind11で公開した電気I/Oインターフェースを
参照します。`sim/bridge.py`のadapterがPythonプラントへ委譲します。

| adapter | Python側の委譲先 | 用途 |
|---|---|---|
| `SensorAdapter` | `sensor.read_voltage()` | 温度、水位、満水、蓋の入力 |
| `ActuatorAdapter` | `actuator.set_control_voltage()` | ヒータrelay、pump、電源の出力 |
| `DutyCycleAdapter` | `heater.set_duty_cycle()` | ヒータPWM出力 |

`PotController` wrapperはC++ controllerの状態、表示、エラー検知、タイマー、ブザー、ボタン操作を
Python APIとして公開します。C++がadapter参照を保持するため、wrapperもadapterの寿命を保持します。

## 4. シミュレーション更新

1 tickは次の順序で進みます。

1. `pot.step(dt)`が前tickの出力を使って物理状態とセンサを更新する
2. `controller.update(dt)`がセンサ電圧を読み、状態遷移と次の出力を決定する
3. scenario runnerがtrigger、action、assertionを処理する
4. Web実行では状態snapshotを接続中の全WebSocket clientへ配信する

シミュレーション状態は可変であり、tickの再実行は冪等ではありません。シナリオを分離する場合は
新しい`Pot`と`PotController`を生成します。

## 5. シナリオ実行

テストケースは複数のscenarioを持ち、各scenarioはsetupと順序付きstepで構成します。
stepはtrigger判定、action、状態伝播、assertionの順に処理されます。

- instant mode: 独立した`Pot`と`PotController`を作成し、同期的に最後まで実行
- realtime mode: `web/app.py`の共有simulation上で指定速度に合わせて実行
- MCP mode: workerがFIFO順に子processを起動し、instant modeで実行

入力仕様は[テストケースJSON記述リファレンス](../api/api.md)を参照してください。

## 6. Webサーバー

`web/app.py`はprocess内に単一の`Simulation`を保持します。WebSocket接続は同じポット状態、速度、
シナリオ選択、結果を共有します。RESTのinstant modeだけは共有状態から独立しています。

WebSocketのbackground taskは`dt = 1.0` simulation秒で更新し、sleep時間を`dt / speed`とします。
速度は物理定数ではなく実時間あたりのtick頻度に作用します。温度履歴は最大600点を保持し、配信時は
直近60点を含めます。

## 7. MCPサーバー

MCPサーバーは次の境界に分かれます。

| コンポーネント | 責務 |
|---|---|
| `TestService` | 入力検証と公開use case、構造化エラー |
| `JobStore` | SQLite上のjob状態、結果、保持期限 |
| `TestWorker` | FIFO取得、取消、再起動時の状態回復 |
| `SourceStore` | manifest、source set、content-addressed blob |
| `SourceWorkspaceAssembler` | baselineと差分から一時source treeを構築 |
| `LocalCMakeBuildExecutor` | CMake configure/buildと成果物cache |
| `TestProcessExecutor` | deadline付き子processでシナリオを実行 |
| `BuildAndTestPipeline` | assembling、building、testingの連携 |

SQLiteはjobとsource metadataを保持し、blobとbuild成果物はfilesystemへ保存します。完了データは
保持期限後に削除されます。詳細は[MCP APIリファレンス](../api/mcp.md)を参照してください。

## 8. セキュリティと制約

- WebとMCPはいずれも認証を実装していない
- WebSocketのsimulationとMCPのjob一覧は利用者ごとに分離されない
- source buildは信頼境界ではなく、投入されたCMakeをローカルで実行する
- MCPのjob実行deadlineとCMake command timeoutは別の設定である
- C++ bindingは`sim/bridge.py`が利用する公開APIとの互換性が必要である

外部公開時はネットワーク分離、認証、TLS、build sandboxを環境側で用意する必要があります。