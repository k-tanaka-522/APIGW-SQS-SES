# AWS Lambda / CloudFormation ルール

## Lambda 共通Layer

すべての Lambda 関数は `@lambda_bootstrap` デコレータを使用する:

```python
from lambda_common.decorator import lambda_bootstrap

@lambda_bootstrap(service_name="alert-mailer")
def lambda_handler(event, context, logger=None):
    # logger は自動注入される
    ...
```

このデコレータにより以下が自動適用される:
- 構造化 JSON ログ出力（START / END / ERROR フェーズ）
- X-Ray トレーシング初期化（`patch_all()`）
- 未捕捉例外のログ出力 + 再raise（SQS リトライに委ねる）

## 環境変数
- `os.environ.get("KEY", "default")` で取得
- 必須の環境変数は `os.environ["KEY"]` で取得（KeyError で早期失敗）
- 共通フィールドは `lambda_common.config.load_common_fields()` を使用

## X-Ray トレーシング
- テスト時は `AWS_XRAY_SDK_ENABLED=false` を設定
- 明示的なサブセグメントが必要な場合は `trace_subsegment()` を使用:

```python
from lambda_common.tracer import trace_subsegment

with trace_subsegment("describe_alarms"):
    resp = cw.describe_alarms(AlarmNames=[name])
```

## CloudFormation テンプレート
- 純粋な CloudFormation を使用（SAM Transform は使わない）
- `template.yaml` にすべてのリソースを定義
- パラメータでメールアドレス等の設定値を外部化
- Lambda コードは S3 バケットからデプロイ
- IAM ロールは最小権限の原則に従う

## エラーハンドリング方針
- 個別レコード処理の失敗 → `log_warn` + `raise`（SQS リトライ）
- extract() が `None` を返す → 送信スキップ（正常フロー）
- sender.send() 失敗 → 例外をそのまま上げる（SQS リトライ）
- DescribeAlarms 失敗 → catch して event 情報で fallback
