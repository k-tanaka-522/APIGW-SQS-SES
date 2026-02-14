# alert-mailer Lambda 関数 詳細設計

## 1. 概要

監視検知イベント（CloudWatch Logs / CloudWatch Alarm / ECS Task State Change）を受信し、
統一フォーマットのメールで通知する Lambda 関数。

### アーキテクチャ上の位置づけ

```
EventBridge / CW Logs Subscription
    ↓
API Gateway (POST /alert)
    ↓
SQS (alert-mailer-queue)
    ↓
★ Lambda (alert-mailer)  ← 本関数
    ↓
SES (メール送信)
```

### ランタイム情報

| 項目 | 値 |
|------|-----|
| ランタイム | Python 3.12 |
| ハンドラ | `handler.lambda_handler` |
| タイムアウト | 60秒 |
| メモリ | 256MB |
| トレーシング | X-Ray Active Tracing |
| Layer | lambda-common（構造化ログ・X-Ray・デコレータ） |

---

## 2. モジュール構成

```
alertmailer/
├── handler.py              # エントリポイント（SQSアンラップ + ルーティング）
├── handlers/               # イベント種別ごとの抽出処理
│   ├── cloudwatch_alarm.py # CloudWatch Alarm State Change → dict
│   ├── cloudwatch_logs.py  # CloudWatch Logs Subscription Filter → dict
│   └── ecs_task.py         # ECS Task State Change → dict | None
├── renderer.py             # 統一フォーマット HTML/テキスト生成
├── sender.py               # SES メール送信
├── utils.py                # ユーティリティ（JST変換等）
└── mappings/               # JSON 定義ファイル
    ├── field_map.json      # 表示項目定義（項目名・キー・表示順）
    └── priority_map.json   # 重要度マッピング（ALARM→危険 等）
```

---

## 3. 処理フロー

### 3.1 全体フロー

```
lambda_handler(event, context)
  │
  ├── SQS Records をループ
  │     │
  │     ├── record["body"] を JSON パース
  │     │
  │     ├── _process(body, context, logger)
  │     │     │
  │     │     ├── classify(body)         # イベント種別判定
  │     │     │     → "cloudwatch_logs" | "cloudwatch_alarm" | "ecs_task"
  │     │     │
  │     │     ├── extract(body, context) # フィールド抽出
  │     │     │     → dict | None
  │     │     │
  │     │     ├── (None なら送信スキップ)
  │     │     │
  │     │     ├── COMMON_FIELDS とマージ
  │     │     │
  │     │     ├── renderer.render()      # HTML/テキスト生成
  │     │     │     → (subject, body_text, body_html)
  │     │     │
  │     │     └── sender.send()          # SES 送信
  │     │
  │     └── time.sleep(1)  # SES 秒間1通制御
  │
  └── (例外発生時は log_warn → raise で SQS リトライ)
```

### 3.2 イベント種別判定（classify）

| 条件 | 種別キー |
|------|---------|
| `"awslogs"` キーが存在 | `cloudwatch_logs` |
| `detail-type == "ECS Task State Change"` かつ `source == "aws.ecs"` | `ecs_task` |
| `detail-type == "CloudWatch Alarm State Change"` かつ `source == "aws.cloudwatch"` | `cloudwatch_alarm` |
| 上記いずれにも該当しない | `ValueError` 送出 |

---

## 4. 各ハンドラ詳細設計

### 4.1 cloudwatch_alarm.py

**入力**: EventBridge CloudWatch Alarm State Change イベント

**処理内容**:

1. `detail.alarmName`、`detail.state` を取得
2. `time` フィールドから UTC タイムスタンプをパース
3. `DescribeAlarms` API を1回呼び出し（exactマッチ）
   - 単一メトリクスアラーム → Namespace、MetricName、Dimensions を取得
   - 複合アラーム → AlarmRule を取得
   - API 失敗時 → イベント情報のみで fallback（例外を握りつぶす）
4. field_map.json のキーに対応する辞書を返す

**設計方針**:
- 複合アラームと単一アラームを同一ロジックで処理
- DescribeAlarms は1回のみ（多段フォールバックしない）
- CloudWatch 側の ActionsSuppressor で発報制御する前提

**出力フィールド**:

| キー | 値の例 | 説明 |
|------|--------|------|
| priority | `ALARM` / `OK` / `INSUFFICIENT_DATA` | アラーム状態 |
| msg_code | `CW-ALARM` | 固定値 |
| monitor_id | `test-cpu-alarm` | アラーム名 |
| monitor_detail | `AWS/EC2/CPUUtilization` | Namespace/MetricName |
| scope | `InstanceId=i-12345` | ディメンション情報 |
| message | `Threshold Crossed: ...` | 状態遷移理由 |
| org_message | `{"version":"1.0",...}` | 遷移理由の JSON 生データ |

### 4.2 cloudwatch_logs.py

**入力**: CloudWatch Logs Subscription Filter イベント（base64 + gzip 圧縮）

**処理内容**:

1. `awslogs.data` を base64 デコード → gzip 展開 → JSON パース
2. logGroup、logStream、logEvents を取得
3. logEvents を全件処理:
   - `message`: 先頭5件を改行結合 + 残件数を付記
   - `org_message`: 全件を `---` 区切りで結合
4. 先頭イベントのタイムスタンプ（ミリ秒UNIX時刻）を JST に変換

**出力フィールド**:

| キー | 値の例 | 説明 |
|------|--------|------|
| priority | `ALARM` | 固定値（ログ検知は常に「危険」） |
| msg_code | `CW-LOGS` | 固定値 |
| monitor_id | `/ecs/my-app` | ロググループ名 |
| monitor_detail | `ecs/container/abc123` | ログストリーム名 |
| message | `ERROR: Connection timeout\n...` | 先頭5件の要約 |
| org_message | 全ログメッセージ | `---` 区切りの全文 |

### 4.3 ecs_task.py

**入力**: EventBridge ECS Task State Change イベント

**処理内容**:

1. `detail.containers` から失敗コンテナを抽出
   - 判定条件: `exitCode not in (0, None)` または `reason` が設定済み
2. **失敗コンテナが0件の場合は `None` を返して送信スキップ**
3. ARN からリソース名を抽出（末尾を `/` で分割）
4. 失敗コンテナの詳細を組み立て
5. ECS コンソールへの直リンクURLを生成

**出力フィールド**:

| キー | 値の例 | 説明 |
|------|--------|------|
| priority | `TASK_STOPPED` | 固定値 |
| msg_code | `ECS-TASK` | 固定値 |
| monitor_id | `my-task:1` | タスク定義名 |
| scope | `my-cluster` | クラスタ名 |
| message | `Essential container in task exited` | 停止理由 |
| org_message | コンテナ詳細 + ECS URL | 失敗コンテナ情報と直リンク |

**None を返すケース**: 失敗コンテナがない場合（正常終了、スケーリング停止等）

---

## 5. renderer.py 詳細設計

### 責務

抽出済みフィールド辞書 + field_map.json + priority_map.json を組み合わせて
メール件名・HTML本文・テキスト本文を生成する。

### 処理内容

1. `priority` キーから `priority_map.json` を参照し、表示ラベルと CSS クラスを解決
2. `field_map.json` の `fields` 定義順に行データを構築
3. 統一フォーマットの HTML テーブル + プレーンテキストを生成

### デザイン仕様

| 要素 | 色 | 説明 |
|------|-----|------|
| thead th | `#0f1c50`（紺） | テーブルヘッダ背景色 |
| tbody th | `#6785c1`（青灰） | 行ヘッダ背景色（項目名列） |
| td.Critical | `#bc4328`（赤） | ALARM / ERROR 時の重要度セル |
| td.Warning | `#e6b600`（黄） | TASK_STOPPED 時の重要度セル |
| td.Info | `#0080b1`（青） | OK 時の重要度セル |
| td.Unknown | `#bc4328`（赤） | INSUFFICIENT_DATA 時の重要度セル |

### 件名フォーマット

```
[重要度ラベル] プラグイン名 : 監視項目ID
```

例: `[危険] CloudWatch Alarm : test-cpu-alarm`

### セキュリティ

すべてのフィールド値は `html.escape()` でエスケープし、XSS を防止する。

---

## 6. sender.py 詳細設計

### 責務

SES を使用して HTML + テキストのマルチパートメールを送信する。

### 環境変数

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `MAIL_FROM` | 必須 | 送信元アドレス |
| `MAIL_TO` | 必須 | 宛先アドレス（カンマ/セミコロン区切り） |
| `MAIL_CC` | 任意 | CC アドレス |
| `MAIL_BCC` | 任意 | BCC アドレス |
| `MAIL_SUBJECT_PREFIX` | 任意 | 件名プレフィックス（デフォルト: 空文字） |

### アドレス分割ロジック

`_split()` 関数がカンマ(`,`)またはセミコロン(`;`)で分割し、前後の空白を除去する。

---

## 7. マッピング定義

### field_map.json

メール本文に表示する項目の順序とラベルを定義する。
`fields` 配列の順序がそのまま表示順序になる。

### priority_map.json

イベントの priority 値から表示ラベルと CSS クラスへの対応を定義する。

| priority 値 | ラベル | CSS クラス |
|------------|--------|-----------|
| `ALARM` | 危険 | Critical |
| `INSUFFICIENT_DATA` | 不明 | Unknown |
| `OK` | 情報 | Info |
| `ERROR` | 危険 | Critical |
| `TASK_STOPPED` | 警戒 | Warning |

---

## 8. エラーハンドリング

| 箇所 | 処理 | 理由 |
|------|------|------|
| `extract()` 例外 | `log_warn` → `raise` | SQS リトライに委ねる |
| `DescribeAlarms` 失敗 | `catch` → event 情報で fallback | 通知自体は可能 |
| `sender.send()` 失敗 | 例外をそのまま上げる | SQS リトライに委ねる |
| `extract()` が `None` 返却 | 送信スキップ | 正常フロー |
| 未捕捉例外 | `decorator.py` で `log_error` → `raise` | SQS リトライに委ねる |

---

## 9. 流量制御

| 設定 | 値 | 説明 |
|------|-----|------|
| SQS BatchSize | 1 | 1回の呼び出しで1メッセージ処理 |
| SQS MaximumConcurrency | 1 | Lambda の同時実行数を1に制限 |
| Lambda 内 sleep | 1秒 | SES の送信レート制限を遵守 |
| SQS VisibilityTimeout | 360秒 | Lambda タイムアウト(60秒)の6倍 |
| DLQ maxReceiveCount | 3 | 3回失敗で DLQ に退避 |

---

## 10. 環境変数一覧

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `ENV_NAME` | 環境表示名 | `-` |
| `FACILITY_NAME` | ファシリティ識別子 | `-` |
| `NOTIFY_DESCRIPTION` | 通知定義ラベル | `-` |
| `MAIL_FROM` | 送信元メールアドレス | (必須) |
| `MAIL_TO` | 送信先メールアドレス | (必須) |
| `MAIL_CC` | CC アドレス | (空) |
| `MAIL_BCC` | BCC アドレス | (空) |
| `MAIL_SUBJECT_PREFIX` | 件名プレフィックス | (空) |
| `AWS_REGION` | AWS リージョン | `ap-northeast-1` |
| `AWS_ACCOUNT_ID` | AWS アカウントID | `-` |
