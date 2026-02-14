---
paths:
  - "lambda/tests/**/*.py"
  - "**/test_*.py"
  - "**/conftest.py"
---

# テスト規約

## フレームワーク
- pytest + moto（AWS サービスモック）
- カバレッジ目標: 90% 以上

## テスト環境の初期化
- `conftest.py` で環境変数を設定（`os.environ` に直接セット）
- X-Ray は `AWS_XRAY_SDK_ENABLED=false` で無効化
- テスト用の固定値: リージョン `ap-northeast-1`、リクエストID `test-request-id-12345`

## fixture パターン
- `mock_context`: Lambda context オブジェクトのモック
- `cloudwatch_alarm_event`: CloudWatch Alarm State Change イベント
- `cloudwatch_logs_event`: base64+gzip 済み CloudWatch Logs イベント
- `ecs_task_event`: 失敗コンテナあり ECS Task イベント
- `ecs_task_event_no_failure`: 失敗コンテナなし ECS Task イベント
- `sqs_wrapped_event`: SQS ラップされたイベント
- `field_map`, `priority_map`: JSON マッピングファイル

## テスト構成
```
lambda/tests/
├── conftest.py              # 共通fixture（全テストで使用）
├── unit_alertmailer/        # alertmailer関数テスト
│   ├── test_handler.py
│   ├── test_cloudwatch_alarm.py
│   ├── test_cloudwatch_logs.py
│   ├── test_ecs_task.py
│   ├── test_renderer.py
│   └── test_sender.py
└── unit_layer/              # Lambda共通Layerテスト
    ├── test_decorator.py
    └── test_logger.py
```

## モックのルール
- boto3 クライアント: `unittest.mock.patch` でモジュールレベルのクライアントをモック
- 外部 API 呼び出し: moto デコレータまたは patch で置き換え
- `time.sleep`: テスト時は patch して即座に返す

## テスト命名
- クラス: `TestXxx`（テスト対象の機能名）
- メソッド: `test_具体的な振る舞い`（日本語コメントで補足可）
- 例: `test_metric_alarm_normal`, `test_no_failure_returns_none`
