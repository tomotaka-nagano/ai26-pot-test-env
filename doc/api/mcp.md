# MCP APIリファレンス

Streamable HTTP MCPサーバーはテストケースを検証し、SQLiteで永続化したFIFOキューで実行します。

```bash
uv run python -m mcp_server.server
```

既定のMCP endpointは`http://127.0.0.1:8001/mcp`、blob upload endpointは
`PUT http://127.0.0.1:8001/blobs/{sha256}`です。

## ToolsとResource

| 名前 | 用途 |
|---|---|
| `validate_test_case(test_case)` | 投入せずにJSONを検証・正規化 |
| `prepare_source_upload(manifest)` | C++差分manifestを検証し、不足blobを取得 |
| `finalize_source_upload(source_set_id)` | 全blob転送後にsource setを確定 |
| `submit_test(test_case, source_set_id?)` | 検証後にテストをFIFOキューへ投入 |
| `get_test_status(test_id)` | 状態を取得 |
| `get_test_result(test_id)` | 完了結果を取得 |
| `cancel_test(test_id)` | queuedを取消、またはrunningの取消を要求 |
| `list_tests(limit?, status?)` | 最近の共有ジョブを一覧取得。`limit`は1から100 |

Resource `pot-sim://docs/test-case-rules`は[テストケースJSON記述リファレンス](api.md)を返します。

ジョブ状態は`queued`、`running`、`completed`、`failed`、`cancelled`、`expired`です。
ソース付きジョブでは`phase`に`assembling`、`building`、`testing`が入ることがあります。
進捗率は返しません。

成功時は`ok: true`、業務エラー時は次の形式を返します。

```json
{
  "ok": false,
  "error": {
    "code": "QUEUE_FULL",
    "message": "The test queue is full.",
    "details": null,
    "retryable": true
  }
}
```

主なエラーコードは`INVALID_SCHEMA`、`NOT_FOUND`、`QUEUE_FULL`、`RESULT_NOT_READY`、
`EXPIRED`、`CANCELLED`、`INVALID_STATE`、`INVALID_SOURCE_MANIFEST`、
`BASE_REVISION_MISMATCH`、`SOURCE_UPLOAD_INCOMPLETE`、`SOURCE_SET_NOT_READY`、
`BUILD_FAILED`、`BUILD_TIMEOUT`、`BINDING_CONTRACT_ERROR`です。

## C++ソースの検証

固定baselineに対するファイル集合をアップロードし、サーバー側で再ビルドしてからテストできます。
転送はcontent-addressed方式で、同じSHA-256のblobは再送不要です。

1. `prepare_source_upload`へmanifestを渡し、`source_set_id`と`missing_blobs`を取得する
2. 不足blobを`PUT /blobs/{sha256}`へraw bytesで送る
3. `finalize_source_upload(source_set_id)`でsource setを確定する
4. `submit_test(test_case, source_set_id)`でビルド・テストを投入する

manifest例:

```json
{
  "base_revision": "baseline",
  "files": [
    {
      "path": "src_cpp/controller/pot_controller.cpp",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "size_bytes": 12000
    }
  ],
  "deleted_paths": []
}
```

対象は`src_cpp/**/*.cpp`、`src_cpp/**/*.h`、`src_cpp/**/*.hpp`、`CMakeLists.txt`です。
`bindings.cpp`も対象ですが、既存Python bridgeとのAPI互換性が必要です。生成Makefile、`build/`、
CMakeキャッシュ、Python拡張などの生成物は受け付けません。

PoCのbuild executorはCMakeをローカルsubprocessで実行します。`CMakeLists.txt`は任意コマンドを
実行できるため、信頼済みコードだけを投入してください。

## 永続化と再起動

結果、入力JSON、詳細イベントは完了から既定24時間後に破棄され、IDと最小状態だけを残して
`expired`になります。再起動時にはqueuedを再開し、runningだったジョブを
`SERVER_RESTARTED`でfailedにします。

## 環境変数

| 環境変数 | 既定値 | 説明 |
|---|---:|---|
| `POT_MCP_HOST` | `127.0.0.1` | リッスンするホスト |
| `POT_MCP_PORT` | `8001` | リッスンするポート |
| `POT_MCP_DB_PATH` | `.data/mcp-tests.sqlite` | SQLite DBのパス |
| `POT_MCP_MAX_QUEUED` | `100` | キュー投入上限 |
| `POT_MCP_EXECUTION_DEADLINE_SECONDS` | `60` | 1ジョブの実時間上限 |
| `POT_MCP_RETENTION_SECONDS` | `86400` | 結果保持期間 |
| `POT_MCP_MAX_REQUEST_BYTES` | `1048576` | MCP要求本文の最大サイズ |
| `POT_MCP_BASE_REVISION` | `baseline` | manifestに要求するbase revision |
| `POT_MCP_BASE_SOURCE_DIR` | `../ai26-pot-application` | 固定baselineとなる外部C++プロジェクト |
| `POT_MCP_BLOB_DIR` | `.data/blobs` | blob保存先 |
| `POT_MCP_MAX_BLOB_BYTES` | `1048576` | 1 blobの最大サイズ |
| `POT_MCP_WORK_DIR` | DBと同じディレクトリの`work/` | ジョブごとの一時ビルド領域 |
| `POT_MCP_ARTIFACT_CACHE_DIR` | DBと同じディレクトリの`artifacts/` | ビルド成果物キャッシュ |
| `POT_MCP_BUILD_TIMEOUT_SECONDS` | `120` | CMakeコマンドごとの実行上限 |

## セキュリティ

初期版は認証なしで、全利用者が同じジョブ一覧と単一キューを共有します。公開ネットワークへ
直接公開せず、閉域または認証とTLSを終端するリバースプロキシの配下で使用してください。