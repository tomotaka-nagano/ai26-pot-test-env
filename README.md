# 話題沸騰ポット GOMA-1015型 シミュレーター

Python製の電気ポットプラントモデルと、C++製の制御アプリケーションを接続する
シミュレーション環境です。Web UIによるリアルタイム操作、REST APIによるシナリオ実行、
MCPサーバーによるFIFOジョブ実行とC++差分の検証を提供します。

## 前提環境

- Python 3.13以上
- [uv](https://docs.astral.sh/uv/)
- CMake
- Python拡張をビルドできるC++コンパイラ
  - Windows: Visual Studio Build Toolsの「C++によるデスクトップ開発」
  - Linux/macOS: 利用環境に対応するC++ツールチェーン
- 外部C++ baselineプロジェクト

外部baselineには、直下の`CMakeLists.txt`と`src_cpp/`が必要です。既定では、このリポジトリの
隣にある`../ai26-pot-application`を参照します。別の場所にある場合は、起動前に
`POT_MCP_BASE_SOURCE_DIR`を設定してください。

```powershell
$env:POT_MCP_BASE_SOURCE_DIR = "C:\path\to\ai26-pot-application"
```

```bash
export POT_MCP_BASE_SOURCE_DIR=/path/to/ai26-pot-application
```

## セットアップ

```bash
uv sync
```

初回のWeb UI起動またはシナリオ実行時に、baselineから`pot_cpp` Python拡張をビルドします。
成果物は既定で`.data/native/artifacts/`へキャッシュされます。baselineまたはツールチェーンが
変わると、別のキャッシュとして再ビルドされます。

## Web UIとREST API

```bash
uv run python -m web.app
```

起動後、以下を利用できます。

- Web UI: <http://127.0.0.1:8000/>
- OpenAPI: <http://127.0.0.1:8000/docs>
- WebSocket: `ws://127.0.0.1:8000/ws`

登録済みシナリオを即時実行する例:

```bash
curl -X POST http://127.0.0.1:8000/api/scenarios/run/sample_tests.json
```

`POST /api/scenarios/run`と`POST /api/scenarios/run/{filename}`は、`mode=instant`（既定）または
`mode=realtime`を受け付けます。realtimeでは`speed=0.5`から`20.0`も指定できます。
REST APIとWebSocketの詳細は[Web APIリファレンス](doc/api/web.md)を参照してください。

## MCPサーバー

```bash
uv run python -m mcp_server.server
```

既定の接続先は`http://127.0.0.1:8001/mcp`です。MCPサーバーはテストケースの検証・投入・
状態照会・結果取得・キャンセル・一覧取得に加え、固定baselineに対するC++差分のアップロードと
ビルドを提供します。

初期版は認証なし・全ジョブ共有です。公開ネットワークへ直接公開せず、閉域または認証とTLSを
終端するリバースプロキシの配下で使用してください。設定、Tools、ソースアップロード手順は
[MCP APIリファレンス](doc/api/mcp.md)を参照してください。

## テスト

単体・結合テストを実行します。

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

ネイティブビルドを伴うテストには、前提環境と外部baselineが必要です。

Webサーバー起動中に、`tests/product/*.json`をREST API経由で一括実行できます。

```bash
uv run python tests/run_all.py
```

## ドキュメント

- [テストケースJSON記述リファレンス](doc/api/api.md)
- [Web APIリファレンス](doc/api/web.md)
- [MCP APIリファレンス](doc/api/mcp.md)
- [システム設計](doc/design/pot.md)
- [製品仕様書（PDF）](doc/ref/pot-spec-v6.pdf)

`doc/api/api.md`はMCP Resource `pot-sim://docs/test-case-rules`としても公開されます。

## プロジェクト構成

```text
mcp_server/    MCPサーバー、ジョブ永続化、ソース転送、ビルド実行
sim/           Pythonプラントモデル、C++ bridge、シナリオ実行・検証
tests/         unittestテストと製品シナリオ
web/           FastAPIサーバーとWeb UI
doc/           API、設計、参照資料
```