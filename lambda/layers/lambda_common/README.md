# lambda_common Layer 詳細設計

## 1. 概要

Lambda 共通 Layer（`lambda-common`）は、複数の Lambda 関数で共有される
基盤機能を提供するレイヤーである。

`@lambda_bootstrap("service-name")` の1行をハンドラに適用するだけで、
以下が自動的に有効化される:

- **構造化ログ出力**: JSON 形式での START / END / ERROR / WARN ログ
- **X-Ray トレーシング**: boto3 の全サービス呼び出しの自動トレース
- **エラーハンドリング**: 未捕捉例外のログ出力 + 再raise

---

## 2. モジュール構成

```
layers/lambda_common/python/lambda_common/
├── __init__.py
├── decorator.py    # @lambda_bootstrap デコレータ
├── logger.py       # 構造化ログ出力（JSON）
├── tracer.py       # X-Ray 初期化・サブセグメント
└── config.py       # 環境変数からの共通設定ロード
```

---

## 3. 各モジュール詳細設計

### 3.1 decorator.py

#### 責務

Lambda ハンドラのラッパーとして、横断的関心事（ログ・トレース・エラーハンドリング）を
透過的に適用する。

#### 処理タイミング

| タイミング | 処理内容 |
|-----------|---------|
| モジュールロード時（コールドスタート） | ロガー生成、X-Ray 初期化 |
| 各呼び出し開始時 | START ログ出力 |
| 正常終了時 | END ログ出力 |
| 例外発生時 | ERROR ログ出力 → 例外を再raise |

#### 使い方

```python
from lambda_common.decorator import lambda_bootstrap

@lambda_bootstrap(service_name="alert-mailer")
def lambda_handler(event, context, logger=None):
    # logger はデコレータが自動注入する
    logger.info("Processing...")
    ...
```

#### logger の注入

デコレータは元の関数を `func(event, context, logger=logger)` で呼び出す。
そのため、ハンドラの引数に `logger=None` を定義しておく必要がある。

#### エラーハンドリング方針

例外発生時は ERROR ログを出力した後、例外をそのまま re-raise する。
これにより SQS のリトライ機構が発動し、処理が再試行される。

---

### 3.2 logger.py

#### 責務

CloudWatch Logs に出力する構造化ログ（JSON形式）を生成する。
全 Lambda で統一されたフォーマットを使用することで、
CloudWatch Logs Insights での横断検索が容易になる。

#### ログフェーズ

| フェーズ | ログレベル | 出力タイミング | 含まれるフィールド |
|---------|-----------|---------------|-------------------|
| `START` | INFO | Lambda 実行開始時 | service, request_id, timestamp, event_summary |
| `END` | INFO | Lambda 正常終了時 | service, request_id, timestamp, status=SUCCESS |
| `WARN` | WARNING | 個別レコード処理の警告時 | service, request_id, timestamp, message |
| `ERROR` | ERROR | Lambda 異常終了時 | service, request_id, timestamp, status=FAILURE, error_type, error_message, stacktrace |

#### ログ出力例

**START ログ**:
```json
{
  "phase": "START",
  "service": "alert-mailer",
  "request_id": "abc-123",
  "timestamp": "2026-02-14T10:00:00+09:00",
  "event_summary": "SQS records=1"
}
```

**ERROR ログ**:
```json
{
  "phase": "ERROR",
  "service": "alert-mailer",
  "request_id": "abc-123",
  "timestamp": "2026-02-14T10:00:01+09:00",
  "status": "FAILURE",
  "error_type": "ValueError",
  "error_message": "Unsupported event",
  "stacktrace": "Traceback (most recent call last):..."
}
```

#### イベント要約ロジック（_summarize_event）

イベント全体をログに出力するとサイズが過大になるため、種別に応じた要約を行う:

| 条件 | 要約文字列 |
|------|-----------|
| `"Records"` キーが存在 | `SQS records=N`（レコード数） |
| `"awslogs"` キーが存在 | `CloudWatch Logs subscription` |
| `"detail-type"` が存在 | detail-type の値そのまま |
| いずれも該当しない | `unknown` |

#### タイムスタンプ

すべてのタイムスタンプは JST（Asia/Tokyo）の ISO 8601 形式で出力する。

---

### 3.3 tracer.py

#### 責務

AWS X-Ray の初期化とサブセグメント作成のユーティリティを提供する。

#### init_tracer()

`aws_xray_sdk.core.patch_all()` を呼び出し、boto3 の全サービス呼び出しを
自動的に X-Ray トレースの対象にする。

- `decorator.py` のモジュールロード時に1回だけ呼び出される
- テスト時は環境変数 `AWS_XRAY_SDK_ENABLED=false` を設定して無効化

#### trace_subsegment(name)

特定の処理区間を X-Ray のサブセグメントとして記録するための
コンテキストマネージャを返す。

```python
from lambda_common.tracer import trace_subsegment

with trace_subsegment("describe_alarms"):
    resp = cw.describe_alarms(AlarmNames=[alarm_name])
```

上記により、X-Ray コンソールで `describe_alarms` というサブセグメントが可視化され、
所要時間やエラー発生の有無を確認できる。

---

### 3.4 config.py

#### 責務

`field_map.json` の `common` セクションに対応するフィールド値を
環境変数から取得して辞書として返す。

#### load_common_fields() の返却値

| キー | 環境変数 | デフォルト | 説明 |
|------|---------|-----------|------|
| `env_name` | `ENV_NAME` | `-` | 環境表示名（例: "本番環境"） |
| `facility_id` | `AWS_ACCOUNT_ID` + `AWS_REGION` | `-` | "アカウントID-リージョン" 形式 |
| `facility_name` | `FACILITY_NAME` | `-` | ファシリティ識別子 |
| `notify_description` | `NOTIFY_DESCRIPTION` | `-` | 通知定義ラベル |

---

## 4. 横展開ガイド

### 新しい Lambda 関数を追加する場合

1. Lambda 関数のハンドラに `@lambda_bootstrap` を適用:

```python
from lambda_common.decorator import lambda_bootstrap

@lambda_bootstrap(service_name="my-new-function")
def lambda_handler(event, context, logger=None):
    logger.info("Processing...")
    ...
```

2. CloudFormation で `CommonLayer` の ARN を Layer として指定:

```yaml
MyNewFunction:
  Type: AWS::Lambda::Function
  Properties:
    Layers:
      - !ImportValue CommonLayerArn  # または直接参照
```

3. テスト時は `AWS_XRAY_SDK_ENABLED=false` を設定して X-Ray を無効化

### メリット

- ログフォーマットが全 Lambda で統一される
- CloudWatch Logs Insights で `service` フィールドによる横断検索が可能
- X-Ray でサービスマップ上に全 Lambda が表示される
- エラーハンドリングのパターンが統一される
