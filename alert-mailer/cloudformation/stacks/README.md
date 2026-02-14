# Alert Mailer - Stack Topology

## Overview

Alert Mailer は5つの CloudFormation スタックで構成される。
各スタックはライフサイクル/リソース種別ごとに分割され、`Fn::ImportValue` で疎結合に連携する。

## Dependency Graph

```
  observability          sqs
  (独立)            ┌─────┴─────┐
                    │           │
                   iam          │
                ┌───┴───┐       │
                │       │       │
             lambda  api-gateway
```

## Deploy Order

| Order | Stack         | Template              | Depends On  | Description                          |
|-------|---------------|-----------------------|-------------|--------------------------------------|
| 1     | sqs           | templates/sqs.yaml    | (なし)      | SQS メインキュー + DLQ              |
| 1     | observability | templates/observability.yaml | (なし) | X-Ray サンプリングルール           |
| 2     | iam           | templates/iam.yaml    | sqs         | Lambda実行ロール + API→SQSロール    |
| 3     | lambda        | templates/lambda.yaml | sqs, iam    | Lambda関数 + 共通Layer + ESM        |
| 3     | api-gateway   | templates/api-gateway.yaml | sqs, iam | REST API + Stage + SQS QueuePolicy |

> Order が同じスタックは並列デプロイ可能。

## Stack Naming Convention

```
${EnvPrefix}-alert-mailer-${component}
```

| Environment | Example                          |
|-------------|----------------------------------|
| dev         | `dev-alert-mailer-sqs`           |
| stg         | `stg-alert-mailer-lambda`        |
| prod        | `prod-alert-mailer-api-gateway`  |

## Exports / Imports

### sqs stack

| Export Name                    | Value          | Imported By           |
|--------------------------------|----------------|-----------------------|
| `${EnvPrefix}-AlertQueueArn`   | Queue ARN      | iam, lambda, api-gateway |
| `${EnvPrefix}-AlertQueueUrl`   | Queue URL      | api-gateway           |
| `${EnvPrefix}-AlertQueueName`  | Queue Name     | api-gateway           |
| `${EnvPrefix}-AlertDLQArn`     | DLQ ARN        | (未使用、将来拡張用)  |
| `${EnvPrefix}-AlertDLQUrl`     | DLQ URL        | (未使用、将来拡張用)  |

### iam stack

| Export Name                    | Value          | Imported By           |
|--------------------------------|----------------|-----------------------|
| `${EnvPrefix}-LambdaRoleArn`   | Lambda Role ARN| lambda                |
| `${EnvPrefix}-ApiRoleArn`      | API Role ARN   | api-gateway           |

### lambda stack

| Export Name                    | Value          | Imported By           |
|--------------------------------|----------------|-----------------------|
| `${EnvPrefix}-FunctionArn`     | Function ARN   | (将来拡張用)          |
| `${EnvPrefix}-LayerArn`        | Layer ARN      | (他のLambdaで共有可)  |

### api-gateway stack

| Export Name                    | Value          | Imported By           |
|--------------------------------|----------------|-----------------------|
| `${EnvPrefix}-ApiEndpoint`     | API URL        | (外部連携用)          |

### observability stack

> Export なし（独立スタック）

## Adding a New Stack

1. `cloudformation/templates/` に新規 YAML テンプレートを作成
2. `EnvPrefix` パラメータを必ず含める
3. Export 名は `${EnvPrefix}-` プレフィックスを付与
4. このファイルの Dependency Graph / Deploy Order / Exports テーブルを更新
5. `scripts/deploy.sh` の `STACKS` 配列にスタックを追加
6. `cloudformation/parameters/*.json` に必要なパラメータを追加
