# Alert Mailer - 監視メール配信システム

## プロジェクト概要

AWS上の監視検知イベント（CloudWatch Logs / CloudWatch Alarm / ECS Task State Change）を
統一フォーマットのメールで通知するシステム。

**アーキテクチャ**: EventBridge/CW Logs Subscription → API Gateway → SQS → Lambda → SES

**詳細仕様**: @alert-mailer-spec.md

## 技術スタック

- **言語**: Python 3.12
- **IaC**: 純粋なCloudFormation（SAMは使用しない）
- **デプロイ**: `aws cloudformation package` / `aws cloudformation deploy`
- **テスト**: pytest + moto（AWSサービスモック）
- **トレーシング**: AWS X-Ray（aws_xray_sdk）
- **リージョン**: ap-northeast-1

## ディレクトリ構成

```
├── lambda/
│   ├── functions/
│   │   └── alertmailer/             # Lambda関数コード
│   │       ├── handler.py           # エントリポイント（SQSアンラップ + ルーティング）
│   │       ├── handlers/            # イベント種別ごとの抽出処理
│   │       │   ├── cloudwatch_logs.py   # extract() → dict
│   │       │   ├── cloudwatch_alarm.py  # extract() → dict
│   │       │   └── ecs_task.py          # extract() → dict | None
│   │       ├── renderer.py          # 統一フォーマット HTML/テキスト生成
│   │       ├── sender.py            # SES送信
│   │       ├── utils.py             # ユーティリティ（JST変換等）
│   │       └── mappings/            # JSON定義ファイル
│   ├── layers/
│   │   └── lambda_common/python/lambda_common/   # 共通Layer
│   │       ├── decorator.py         # @lambda_bootstrap デコレータ
│   │       ├── logger.py            # 構造化ログ（プレーンテキスト）
│   │       ├── tracer.py            # X-Ray初期化
│   │       └── config.py            # 環境変数ロード
│   └── tests/
│       ├── conftest.py              # 共通fixture
│       ├── unit_alertmailer/        # alertmailer関数テスト
│       └── unit_layer/              # 共通Layerテスト
├── cloudformation/
│   ├── templates/                   # CFnテンプレート（5スタック分割）
│   │   ├── sqs.yaml                 # SQS + DLQ
│   │   ├── iam.yaml                 # IAMロール
│   │   ├── lambda.yaml              # Lambda + Layer + ESM
│   │   ├── api-gateway.yaml         # API Gateway + QueuePolicy
│   │   └── observability.yaml       # X-Ray
│   ├── stacks/                      # スタックトポロジー文書
│   │   └── README.md
│   └── parameters/                  # 環境別パラメータ
│       ├── dev.json
│       ├── stg.json
│       └── prod.json
├── scripts/
│   └── deploy.sh                    # マルチスタックデプロイスクリプト
└── events/                          # テスト用SQSイベントサンプル
```

## コマンド

```bash
# テスト実行
pytest lambda/tests/ -v

# カバレッジ付きテスト
pytest lambda/tests/ --cov=lambda --cov-report=term-missing

# CloudFormation テンプレート検証（全テンプレート）
for f in cloudformation/templates/*.yaml; do
  aws cloudformation validate-template --template-body file://$f
done

# デプロイ（環境指定: dev/stg/prod）
bash scripts/deploy.sh dev
```

## 設計上の重要な方針

1. **複合アラーム**: Lambda側で子アラーム展開は行わない。CloudWatch ActionsSuppressorで制御する前提
2. **DescribeAlarms**: 1回（exactマッチ）のみ。失敗時はevent情報でfallback
3. **CloudWatch Logs**: 全logEventsを処理（message: 先頭5件+残件数、org_message: 全件）
4. **ECS Task**: 失敗コンテナがない場合は `None` を返して送信スキップ
5. **エラーハンドリング**: 例外はSQSリトライに委ねるため再raise
6. **流量制御**: SQS BatchSize=1, MaximumConcurrency=1, Lambda内 sleep(1)
7. **Lambda共通Layer**: `@lambda_bootstrap("service-name")` の1行で構造化ログ + X-Ray + エラーハンドリング

## 環境変数

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `ENV_NAME` | 環境表示名 | 本番環境 |
| `FACILITY_NAME` | ファシリティ識別子 | aws-production |
| `MAIL_FROM` | 送信元メールアドレス | (必須) |
| `MAIL_TO` | 送信先メールアドレス（カンマ区切り） | (必須) |
| `MAIL_CC` | CC（カンマ区切り） | (空) |
| `MAIL_BCC` | BCC（カンマ区切り） | (空) |
| `MAIL_SUBJECT_PREFIX` | 件名プレフィックス | [AWS監視]  |
| `NOTIFY_DESCRIPTION` | 通知定義ラベル | AWS監視 通知定義 (メール送信) |
