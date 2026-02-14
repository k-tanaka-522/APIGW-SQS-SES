# 監視メール配信Lambda リファクタリング指示書

## 1. 概要

### 背景
現在、AWS上でCloudWatch Logs / CloudWatch Alarm / ECS Task State Changeの監視検知→メール通知をLambda（MonitoringMail）で実行している。既存監視ツールからの通知メールと形式が異なり、運用チームが見づらい状態にある。本リファクタリングでは以下を実施する。

### 目的
1. モジュール分割によるメンテナンス性の向上
2. 既存監視ツールのメールとのデザイン・項目統一
3. API Gateway → SQS → Lambda → SES 構成への移行（非同期化・流量制御）
4. X-Rayによるエンドツーエンドトレーシング
5. Lambda共通処理のLayer化（複数Lambda横展開前提）
6. テスト自動化（pytest + moto）

---

## 2. アーキテクチャ

### 構成図
```
[EventBridge / CloudWatch Logs Subscription]
        ↓
   API Gateway (REST API / POST)
        ↓
   SQS (標準キュー / バッファ)
        ↓
   Lambda (alert-mailer)
        ↓
   SES (メール送信)
```

### SQS設計
- 受付: 無制限（SQSが吸収）
- 流量制御: Lambda Event Source Mappingで制御
  - `BatchSize: 1`
  - `MaximumConcurrency: 1`
  - Lambda内で `time.sleep(1)` により秒間1通制御
- `VisibilityTimeout`: Lambdaタイムアウトの6倍（360秒）
- DLQ: 3回失敗で退避

### X-Ray
- API Gateway: ステージでX-Rayトレーシング有効化
- Lambda: Active Tracing有効化
- サンプリングレート: 100%（監視メールは低トラフィック）
- Lambda内で `aws_xray_sdk` による `patch_all()` でSES呼び出しもトレース

### IaC
- 純粋なCloudFormation（SAMは使用しない）
- `aws cloudformation package` / `aws cloudformation deploy` でデプロイ

---

## 3. ディレクトリ構成

```
alert-mailer/
├── template.yaml                    # CloudFormation テンプレート
├── deploy.sh                        # パッケージング + デプロイスクリプト
├── layers/
│   └── lambda_common/
│       └── python/
│           └── lambda_common/
│               ├── __init__.py
│               ├── decorator.py     # @lambda_bootstrap デコレータ
│               ├── logger.py        # 構造化ログ（START/END/ERROR）
│               ├── tracer.py        # X-Ray初期化 + patch_all
│               └── config.py        # 共通環境変数ロード
├── functions/
│   └── alert_mailer/
│       ├── __init__.py
│       ├── handler.py               # エントリポイント（SQSアンラップ + ルーティング）
│       ├── handlers/
│       │   ├── __init__.py
│       │   ├── cloudwatch_logs.py   # extract() → dict
│       │   ├── cloudwatch_alarm.py  # extract() → dict
│       │   └── ecs_task.py          # extract() → dict
│       ├── renderer.py              # dict + field_map → HTML/テキスト生成
│       ├── sender.py                # SES送信
│       ├── utils.py                 # format_jst, _split_emails等
│       └── mappings/
│           ├── field_map.json       # 統一フォーマット 項目マッピング
│           └── priority_map.json    # 重要度マッピング
├── events/
│   ├── sqs_cloudwatch_alarm.json    # テスト用イベント
│   ├── sqs_cloudwatch_logs.json
│   ├── sqs_ecs_task.json
│   └── sqs_alarm_composite.json
└── tests/
    ├── __init__.py
    ├── conftest.py                  # 共通fixture（環境変数、モック等）
    ├── unit/
    │   ├── __init__.py
    │   ├── test_handler.py
    │   ├── test_cloudwatch_alarm.py
    │   ├── test_cloudwatch_logs.py
    │   ├── test_ecs_task.py
    │   ├── test_renderer.py
    │   └── test_sender.py
    └── unit_common/
        ├── __init__.py
        ├── test_decorator.py
        └── test_logger.py
```

---

## 4. Lambda共通Layer（横展開前提）

### 設計思想

どのLambdaでも `@lambda_bootstrap("service-name")` の1行で以下が自動適用される:
- 構造化ログ出力（START / END / ERROR）
- X-Rayトレーシング初期化
- 未捕捉例外のログ出力 + 再raise（SQSリトライに委ねる）

今後Lambda関数を増やす際も、このLayerを共有するだけで統一的なログ・トレースが入る。

### 4.1 decorator.py

```python
from functools import wraps
from lambda_common.logger import get_logger, log_start, log_end, log_error
from lambda_common.tracer import init_tracer


def lambda_bootstrap(service_name: str):
    """
    1行でログ・X-Ray・エラーハンドリングを初期化するデコレータ。
    
    使い方:
        @lambda_bootstrap(service_name="alert-mailer")
        def lambda_handler(event, context, logger=None):
            ...
    """
    def decorator(func):
        logger = get_logger(service_name)
        init_tracer()

        @wraps(func)
        def wrapper(event, context):
            request_id = context.aws_request_id
            log_start(logger, service_name, request_id, event)
            try:
                result = func(event, context, logger=logger)
                log_end(logger, service_name, request_id)
                return result
            except Exception as e:
                log_error(logger, service_name, request_id, e)
                raise  # SQSリトライに委ねるため再raise
        return wrapper
    return decorator
```

### 4.2 logger.py

```python
import logging
import json
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def get_logger(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    return logger


def _now_jst() -> str:
    return datetime.now(timezone.utc).astimezone(JST).isoformat()


def _summarize_event(event: dict) -> str:
    """イベント全体をログに吐くと大きいので要約"""
    if "Records" in event:
        return f"SQS records={len(event['Records'])}"
    if "awslogs" in event:
        return "CloudWatch Logs subscription"
    detail_type = event.get("detail-type", "")
    if detail_type:
        return detail_type
    return "unknown"


def log_start(logger: logging.Logger, service_name: str,
              request_id: str, event: dict) -> None:
    """Lambda実行開始ログ"""
    logger.info(json.dumps({
        "phase": "START",
        "service": service_name,
        "request_id": request_id,
        "timestamp": _now_jst(),
        "event_summary": _summarize_event(event),
    }, ensure_ascii=False))


def log_end(logger: logging.Logger, service_name: str,
            request_id: str) -> None:
    """Lambda正常終了ログ"""
    logger.info(json.dumps({
        "phase": "END",
        "service": service_name,
        "request_id": request_id,
        "timestamp": _now_jst(),
        "status": "SUCCESS",
    }, ensure_ascii=False))


def log_error(logger: logging.Logger, service_name: str,
              request_id: str, error: Exception) -> None:
    """Lambda異常終了ログ（スタックトレース付き）"""
    logger.error(json.dumps({
        "phase": "ERROR",
        "service": service_name,
        "request_id": request_id,
        "timestamp": _now_jst(),
        "status": "FAILURE",
        "error_type": type(error).__name__,
        "error_message": str(error),
        "stacktrace": traceback.format_exc(),
    }, ensure_ascii=False))


def log_warn(logger: logging.Logger, service_name: str,
             request_id: str, message: str) -> None:
    """個別レコード処理の警告ログ（処理続行時に使用）"""
    logger.warning(json.dumps({
        "phase": "WARN",
        "service": service_name,
        "request_id": request_id,
        "timestamp": _now_jst(),
        "message": message,
    }, ensure_ascii=False))
```

### 4.3 tracer.py

```python
from aws_xray_sdk.core import patch_all, xray_recorder


def init_tracer():
    """X-Ray初期化。boto3の全サービス呼び出しを自動トレース"""
    patch_all()


def trace_subsegment(name: str):
    """
    明示的にサブセグメントを切りたい場合に使用。
    
    使い方:
        with trace_subsegment("describe_alarms"):
            resp = cw.describe_alarms(AlarmNames=[name])
    """
    return xray_recorder.in_subsegment(name)
```

### 4.4 config.py

```python
import os


def load_common_fields() -> dict:
    """field_map.json の common セクションに対応する値を環境変数から取得"""
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    account_id = os.environ.get("AWS_ACCOUNT_ID", "-")
    return {
        "env_name": os.environ.get("ENV_NAME", "-"),
        "facility_id": f"{account_id}-{region}",
        "facility_name": os.environ.get("FACILITY_NAME", "-"),
        "notify_description": os.environ.get("NOTIFY_DESCRIPTION", "-"),
    }
```

### 4.5 別Lambda追加時の使い方

```python
# 例: 別の新しいLambda関数
from lambda_common.decorator import lambda_bootstrap

@lambda_bootstrap(service_name="batch-scheduler")
def lambda_handler(event, context, logger=None):
    # ここに処理を書くだけ。
    # START/END/ERRORログ、X-Rayは自動。
    logger.info("Processing batch...")
    ...
```

---

## 5. 各モジュール仕様

### 5.1 handler.py（エントリポイント）

責務: SQSレコードのアンラップ → イベント種別判定 → 適切なhandlerへディスパッチ → レンダリング → 送信

```python
import json
import time
from lambda_common.decorator import lambda_bootstrap
from lambda_common.config import load_common_fields
from handlers.cloudwatch_logs import extract as extract_logs
from handlers.cloudwatch_alarm import extract as extract_alarm
from handlers.ecs_task import extract as extract_ecs
import renderer
import sender

FIELD_MAP = json.load(open("mappings/field_map.json"))
PRIORITY_MAP = json.load(open("mappings/priority_map.json"))
COMMON_FIELDS = load_common_fields()

DISPATCHERS = {
    "cloudwatch_logs": extract_logs,
    "cloudwatch_alarm": extract_alarm,
    "ecs_task": extract_ecs,
}


@lambda_bootstrap(service_name="alert-mailer")
def lambda_handler(event, context, logger=None):
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        try:
            _process(body, context, logger)
        except Exception as e:
            from lambda_common.logger import log_warn
            log_warn(logger, "alert-mailer", context.aws_request_id,
                     f"Record processing failed: {e}")
            raise  # SQSリトライに委ねる
        time.sleep(1)  # SES秒間1通制御


def _process(body, context, logger):
    event_type = classify(body)
    extractor = DISPATCHERS[event_type]
    fields = extractor(body, context)

    if fields is None:
        logger.info("extract returned None. Skipping.")
        return

    fields = {**COMMON_FIELDS, **fields}
    subject, body_text, body_html = renderer.render(fields, FIELD_MAP, PRIORITY_MAP)
    sender.send(subject, body_text, body_html)


def classify(event: dict) -> str:
    """イベント種別を判定して文字列キーを返す"""
    if "awslogs" in event:
        return "cloudwatch_logs"
    detail_type = event.get("detail-type")
    source = event.get("source")
    if detail_type == "ECS Task State Change" and source == "aws.ecs":
        return "ecs_task"
    if detail_type == "CloudWatch Alarm State Change" and source == "aws.cloudwatch":
        return "cloudwatch_alarm"
    raise ValueError(f"Unsupported event: detail-type={detail_type}, source={source}")
```

### 5.2 handlers/*.py（イベント抽出）

各handlerは `extract(event, context) -> dict | None` を実装。戻り値のキーは `field_map.json` の `key` に対応。Noneを返すと送信スキップ。

#### cloudwatch_logs.py
```python
import base64
import json
import zlib
from utils import format_jst
from datetime import datetime, timezone


def extract(event: dict, context) -> dict:
    """
    CloudWatch Logs Subscription Filterイベントからフィールドを抽出。
    logEventsは全件処理する。
    """
    data = zlib.decompress(
        base64.b64decode(event["awslogs"]["data"]),
        16 + zlib.MAX_WBITS
    )
    data_json = json.loads(data)

    log_group = data_json.get("logGroup", "")
    log_stream = data_json.get("logStream", "")
    log_events = data_json.get("logEvents", [])

    # 全件処理（先頭5件をメッセージに、全件をオリジナルメッセージに）
    messages = [e.get("message", "") for e in log_events]
    summary = "\n".join(messages[:5])
    if len(messages) > 5:
        summary += f"\n... 他 {len(messages) - 5} 件"

    full_messages = "\n---\n".join(messages)

    # 先頭イベントのタイムスタンプをJSTに変換
    first_ts = log_events[0].get("timestamp", 0) if log_events else 0
    dt_utc = datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc)

    return {
        "priority": "ALARM",
        "msg_code": "CW-LOGS",
        "plugin_name": "CloudWatch Logs",
        "monitor_id": log_group,
        "monitor_detail": log_stream,
        "monitor_description": f"ログ監視 ({log_group})",
        "scope": log_group,
        "generation_date": format_jst(dt_utc),
        "application": f"ログ監視 ({log_group})",
        "message": summary,
        "org_message": full_messages,
        "notify_uuid": context.aws_request_id,
    }
```

#### cloudwatch_alarm.py
```python
import boto3
import os
from utils import format_jst
from datetime import datetime, timezone
from lambda_common.tracer import trace_subsegment

cw = boto3.client("cloudwatch",
                   region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))


def extract(event: dict, context) -> dict:
    """
    CloudWatch Alarm State Changeイベントからフィールドを抽出。

    ★設計方針:
    - 複合アラームも単一アラームも同じ処理にする
    - CloudWatch側でActionsSuppressorによる発報制御を前提とし、
      Lambda側では子アラーム展開やDescribeAlarms多段フォールバックは行わない
    - DescribeAlarmsは1回（exactマッチ）のみ。失敗時はevent情報でfallback
    """
    detail = event.get("detail", {})
    alarm_name = detail.get("alarmName", "")
    state = detail.get("state", {})
    region = event.get("region", os.environ.get("AWS_REGION", "ap-northeast-1"))

    # タイムスタンプ
    event_time = event.get("time", "")
    try:
        dt_utc = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    except Exception:
        dt_utc = datetime.now(timezone.utc)

    # DescribeAlarms: 1回だけ（exactマッチのみ）
    namespace = ""
    metric_name = ""
    alarm_description = ""
    dims_str = "-"

    try:
        with trace_subsegment("describe_alarms"):
            resp = cw.describe_alarms(AlarmNames=[alarm_name])
        if resp.get("MetricAlarms"):
            info = resp["MetricAlarms"][0]
            namespace = info.get("Namespace", "")
            metric_name = info.get("MetricName", "")
            alarm_description = info.get("AlarmDescription", "")
            dims = info.get("Dimensions", [])
            dims_str = ", ".join(f"{d['Name']}={d['Value']}" for d in dims) if dims else "-"
        elif resp.get("CompositeAlarms"):
            info = resp["CompositeAlarms"][0]
            alarm_description = info.get("AlarmDescription", "")
            namespace = "Composite"
            metric_name = info.get("AlarmRule", "")
    except Exception:
        pass  # fallback: event情報のみで構成

    return {
        "priority": state.get("value", "ALARM"),
        "msg_code": "CW-ALARM",
        "plugin_name": "CloudWatch Alarm",
        "monitor_id": alarm_name,
        "monitor_detail": f"{namespace}/{metric_name}" if metric_name else alarm_name,
        "monitor_description": alarm_description or f"CloudWatch Alarm ({alarm_name})",
        "scope": dims_str,
        "generation_date": format_jst(dt_utc),
        "application": namespace or "CloudWatch",
        "message": state.get("reason", ""),
        "org_message": state.get("reasonData", ""),
        "notify_uuid": context.aws_request_id,
    }
```

#### ecs_task.py
```python
import os
from utils import format_jst
from datetime import datetime, timezone


def extract(event: dict, context) -> dict | None:
    """
    ECS Task State Changeイベントからフィールドを抽出。
    失敗コンテナがない場合はNoneを返す（送信スキップ）。
    """
    detail = event.get("detail", {})
    containers = detail.get("containers", [])
    failed = [
        c for c in containers
        if c.get("exitCode") not in (0, None) or c.get("reason")
    ]

    if not failed:
        return None

    cluster_arn = detail.get("clusterArn", "")
    task_arn = detail.get("taskArn", "")
    task_def_arn = detail.get("taskDefinitionArn", "")
    stopped_reason = detail.get("stoppedReason", "")

    cluster_name = cluster_arn.split("/")[-1] if cluster_arn else ""
    task_id = task_arn.split("/")[-1] if task_arn else ""
    task_def_name = task_def_arn.split("/")[-1] if task_def_arn else ""

    # タイムスタンプ
    event_time = event.get("time", "")
    try:
        dt_utc = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    except Exception:
        dt_utc = datetime.now(timezone.utc)

    # 失敗コンテナ詳細
    failed_lines = []
    for c in failed:
        failed_lines.append(
            f"Container: {c.get('name')} / "
            f"exitCode: {c.get('exitCode')} / "
            f"reason: {c.get('reason', '')}"
        )
    failed_detail = "\n".join(failed_lines)

    # ECSコンソールURL
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    ecs_url = ""
    if cluster_name and task_id:
        ecs_url = (
            f"https://{region}.console.aws.amazon.com/ecs/v2/clusters/"
            f"{cluster_name}/tasks/{task_id}/details?region={region}"
        )

    org_message = failed_detail
    if ecs_url:
        org_message += f"\n\nECS Task URL:\n{ecs_url}"

    return {
        "priority": "TASK_STOPPED",
        "msg_code": "ECS-TASK",
        "plugin_name": "ECS Task Monitor",
        "monitor_id": task_def_name,
        "monitor_detail": stopped_reason,
        "monitor_description": f"ECSタスク監視 ({task_def_name})",
        "scope": cluster_name,
        "generation_date": format_jst(dt_utc),
        "application": f"ECS ({cluster_name}/{task_def_name})",
        "message": stopped_reason,
        "org_message": org_message,
        "notify_uuid": context.aws_request_id,
    }
```

### 5.3 renderer.py（HTML/テキスト生成）

責務: `fields` dict + `field_map.json` + `priority_map.json` → 統一スタイルのHTML + プレーンテキスト

#### デザイン仕様（HTMLメールテンプレート）

以下の既存監視ツールのメールのCSSスタイルに準拠する:

```css
table { border-spacing: 0; border: none; }
table thead tr th {
    padding: .3em;
    border-bottom: 1px solid #0f1c50;
    background-color: #0f1c50;
    color: #fff;
}
table tbody tr th {
    padding: .3em;
    border-bottom: 1px solid #0f1c50;
    background-color: #6785c1;
    text-align: right;
    color: #fff;
}
table tbody tr td {
    padding: .3em;
    border-bottom: 1px solid #0f1c50;
}
td.Critical { background-color: #bc4328; font-weight: 700; font-size: large; color: #fff; }
td.Warning  { background-color: #e6b600; font-weight: 700; font-size: large; }
td.Info     { background-color: #0080b1; font-weight: 700; font-size: large; color: #fff; }
td.Unknown  { background-color: #bc4328; font-weight: 700; font-size: large; color: #fff; }
```

テーブル構造は統一フォーマットの2カラム形式:
- thead: 「項目名」「通知内容」のヘッダ行
- tbody: field_map.json の fields 順に行を生成
- 重要度セルにはCSSクラス（Critical/Warning/Info/Unknown）を適用
- オリジナルメッセージはpre要素でラップ

```python
import html as html_mod


def render(fields: dict, field_map: dict, priority_map: dict) -> tuple[str, str, str]:
    """
    Returns: (subject, body_text, body_html)
    """
    priority_key = fields.get("priority", "")
    priority_info = priority_map.get(priority_key, {"label": "不明", "css_class": "Unknown"})
    fields["priority_label"] = priority_info["label"]
    fields["priority_css_class"] = priority_info["css_class"]

    rows = []
    for f in field_map["fields"]:
        value = fields.get(f["key"], "-")
        rows.append({"label": f["label"], "value": value, "key": f["key"]})

    body_html = _build_html(rows, fields)
    body_text = _build_plain_text(rows, fields)
    subject = _build_subject(fields)

    return subject, body_text, body_html


def _build_subject(fields: dict) -> str:
    plugin = fields.get("plugin_name", "")
    monitor_id = fields.get("monitor_id", "")
    priority_label = fields.get("priority_label", "")
    return f"[{priority_label}] {plugin} : {monitor_id}"


def _build_plain_text(rows: list[dict], fields: dict) -> str:
    lines = []
    for r in rows:
        lines.append(f"{r['label']}: {r['value']}")
    return "\n".join(lines)


def _build_html(rows: list[dict], fields: dict) -> str:
    """統一フォーマットと同じテーブルデザインのHTMLを生成"""
    css_class = fields.get("priority_css_class", "Unknown")
    env_name = html_mod.escape(fields.get("env_name", ""))
    plugin_name = html_mod.escape(fields.get("plugin_name", ""))

    tbody_rows = ""
    for r in rows:
        value_escaped = html_mod.escape(str(r["value"]))
        if r["key"] == "priority_label":
            td = f'<td class="{css_class}"><pre>{value_escaped}</pre></td>'
        else:
            td = f'<td><pre>{value_escaped}</pre></td>'
        tbody_rows += f"<tr><th>{html_mod.escape(r['label'])}</th>{td}</tr>\n"

    return f"""\
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8" />
<title>{env_name} {plugin_name} 通知</title>
<style>
table{{border-spacing:0;border:none}}
table thead tr th{{padding:.3em;border-bottom:1px solid #0f1c50;background-color:#0f1c50;color:#fff}}
table tbody tr th{{padding:.3em;border-bottom:1px solid #0f1c50;background-color:#6785c1;text-align:right;color:#fff}}
table tbody tr td{{padding:.3em;border-bottom:1px solid #0f1c50}}
td.Critical{{background-color:#bc4328;font-weight:700;font-size:large;color:#fff}}
td.Warning{{background-color:#e6b600;font-weight:700;font-size:large}}
td.Info{{background-color:#0080b1;font-weight:700;font-size:large;color:#fff}}
td.Unknown{{background-color:#bc4328;font-weight:700;font-size:large;color:#fff}}
</style>
</head>
<body>
<main><article>
<p>{env_name}にて以下のイベントが発生しました。<br>通知内容を確認の上、対応を行ってください。</p>
<table cellspacing="0">
<thead><tr><th>項目名</th><th>通知内容</th></tr></thead>
<tbody>
{tbody_rows}
</tbody>
</table>
</article></main>
</body>
</html>
"""
```

### 5.4 sender.py（SES送信）

```python
import boto3
import os

ses = boto3.client("ses", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))


def send(subject: str, body_text: str, body_html: str) -> None:
    to_addrs = _split(os.environ["MAIL_TO"])
    cc_addrs = _split(os.environ.get("MAIL_CC", ""))
    bcc_addrs = _split(os.environ.get("MAIL_BCC", ""))
    mail_from = os.environ["MAIL_FROM"]
    prefix = os.environ.get("MAIL_SUBJECT_PREFIX", "")

    ses.send_email(
        Source=mail_from,
        Destination={
            "ToAddresses": to_addrs,
            "CcAddresses": cc_addrs,
            "BccAddresses": bcc_addrs,
        },
        Message={
            "Subject": {"Data": f"{prefix}{subject}", "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": body_text, "Charset": "UTF-8"},
                "Html": {"Data": body_html, "Charset": "UTF-8"},
            },
        },
    )


def _split(s: str) -> list[str]:
    if not s:
        return []
    return [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]
```

### 5.5 utils.py

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def format_jst(dt_utc: datetime) -> str:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(JST).strftime("%Y/%m/%d %H:%M:%S")
```

### 5.6 mappings/field_map.json

```json
{
  "common": {
    "env_name": "${ENV_NAME}",
    "facility_id": "${AWS_ACCOUNT_ID}-${AWS_REGION}",
    "facility_name": "${FACILITY_NAME}",
    "notify_description": "${NOTIFY_DESCRIPTION}"
  },
  "fields": [
    {"key": "env_name",            "label": "環境"},
    {"key": "priority_label",      "label": "重要度"},
    {"key": "msg_code",            "label": "MSGコード"},
    {"key": "plugin_name",         "label": "プラグイン名"},
    {"key": "monitor_id",          "label": "監視項目ID"},
    {"key": "monitor_detail",      "label": "監視詳細"},
    {"key": "monitor_description", "label": "監視項目説明"},
    {"key": "scope",               "label": "スコープ"},
    {"key": "generation_date",     "label": "出力日時"},
    {"key": "application",         "label": "アプリケーション"},
    {"key": "message",             "label": "メッセージ"},
    {"key": "org_message",         "label": "オリジナルメッセージ"},
    {"key": "notify_description",  "label": "通知説明"},
    {"key": "notify_uuid",         "label": "通知UUID"},
    {"key": "facility_id",         "label": "ファシリティID"},
    {"key": "facility_name",       "label": "ファシリティ名"}
  ]
}
```

### 5.7 mappings/priority_map.json

```json
{
  "ALARM":             {"label": "危険", "css_class": "Critical"},
  "INSUFFICIENT_DATA": {"label": "不明", "css_class": "Unknown"},
  "OK":                {"label": "情報", "css_class": "Info"},
  "ERROR":             {"label": "危険", "css_class": "Critical"},
  "TASK_STOPPED":      {"label": "警戒", "css_class": "Warning"}
}
```

---

## 6. CloudFormationテンプレート (template.yaml)

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: Alert Mailer - Monitoring notification with unified notification format

Parameters:
  EnvName:
    Type: String
    Default: "本番環境"
  FacilityName:
    Type: String
    Default: "aws-production"
  MailFrom:
    Type: String
  MailTo:
    Type: String
  MailCc:
    Type: String
    Default: ""
  MailBcc:
    Type: String
    Default: ""
  MailSubjectPrefix:
    Type: String
    Default: "[AWS監視] "
  NotifyDescription:
    Type: String
    Default: "AWS監視 通知定義 (メール送信)"
  LambdaCodeS3Bucket:
    Type: String
    Description: "Lambda code and layer zip upload bucket"
  AlertMailerCodeS3Key:
    Type: String
    Default: "lambda/alert-mailer.zip"
  CommonLayerS3Key:
    Type: String
    Default: "lambda/lambda-common-layer.zip"

Resources:

  # ============================================================
  # Lambda Layer (共通処理 - 横展開用)
  # ============================================================
  CommonLayer:
    Type: AWS::Lambda::LayerVersion
    Properties:
      LayerName: lambda-common
      Description: "共通処理Layer (logger/tracer/decorator) - 複数Lambdaで共有"
      Content:
        S3Bucket: !Ref LambdaCodeS3Bucket
        S3Key: !Ref CommonLayerS3Key
      CompatibleRuntimes:
        - python3.12
      CompatibleArchitectures:
        - x86_64

  # ============================================================
  # IAM Role (Lambda実行ロール)
  # ============================================================
  AlertMailerRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: alert-mailer-role
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
        - arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess
      Policies:
        - PolicyName: alert-mailer-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - sqs:ReceiveMessage
                  - sqs:DeleteMessage
                  - sqs:GetQueueAttributes
                Resource: !GetAtt AlertQueue.Arn
              - Effect: Allow
                Action:
                  - ses:SendEmail
                  - ses:SendRawEmail
                Resource: "*"
              - Effect: Allow
                Action:
                  - cloudwatch:DescribeAlarms
                Resource: "*"

  # ============================================================
  # Lambda Function
  # ============================================================
  AlertMailerFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: alert-mailer
      Runtime: python3.12
      Handler: handler.lambda_handler
      Code:
        S3Bucket: !Ref LambdaCodeS3Bucket
        S3Key: !Ref AlertMailerCodeS3Key
      Role: !GetAtt AlertMailerRole.Arn
      Timeout: 60
      MemorySize: 256
      TracingConfig:
        Mode: Active
      Layers:
        - !Ref CommonLayer
      Environment:
        Variables:
          ENV_NAME: !Ref EnvName
          FACILITY_NAME: !Ref FacilityName
          NOTIFY_DESCRIPTION: !Ref NotifyDescription
          MAIL_FROM: !Ref MailFrom
          MAIL_TO: !Ref MailTo
          MAIL_CC: !Ref MailCc
          MAIL_BCC: !Ref MailBcc
          MAIL_SUBJECT_PREFIX: !Ref MailSubjectPrefix

  # ============================================================
  # SQS
  # ============================================================
  AlertQueue:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: alert-mailer-queue
      VisibilityTimeout: 360
      MessageRetentionPeriod: 86400
      RedrivePolicy:
        deadLetterTargetArn: !GetAtt AlertDLQ.Arn
        maxReceiveCount: 3

  AlertDLQ:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: alert-mailer-dlq
      MessageRetentionPeriod: 1209600

  AlertQueuePolicy:
    Type: AWS::SQS::QueuePolicy
    Properties:
      Queues:
        - !Ref AlertQueue
      PolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: apigateway.amazonaws.com
            Action: sqs:SendMessage
            Resource: !GetAtt AlertQueue.Arn

  # Lambda Event Source Mapping (SQS → Lambda)
  AlertMailerEventSourceMapping:
    Type: AWS::Lambda::EventSourceMapping
    Properties:
      EventSourceArn: !GetAtt AlertQueue.Arn
      FunctionName: !Ref AlertMailerFunction
      BatchSize: 1
      MaximumConcurrency: 1
      Enabled: true

  # ============================================================
  # API Gateway (REST API)
  # ============================================================
  AlertApi:
    Type: AWS::ApiGateway::RestApi
    Properties:
      Name: alert-mailer-api
      Description: "Alert Mailer API - routes events to SQS"
      EndpointConfiguration:
        Types:
          - REGIONAL

  AlertResource:
    Type: AWS::ApiGateway::Resource
    Properties:
      RestApiId: !Ref AlertApi
      ParentId: !GetAtt AlertApi.RootResourceId
      PathPart: alert

  ApiToSqsRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: api-to-sqs-role
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: apigateway.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: sqs-send
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action: sqs:SendMessage
                Resource: !GetAtt AlertQueue.Arn

  AlertPostMethod:
    Type: AWS::ApiGateway::Method
    Properties:
      RestApiId: !Ref AlertApi
      ResourceId: !Ref AlertResource
      HttpMethod: POST
      AuthorizationType: NONE
      Integration:
        Type: AWS
        IntegrationHttpMethod: POST
        Uri: !Sub "arn:aws:apigateway:${AWS::Region}:sqs:path/${AWS::AccountId}/${AlertQueue.QueueName}"
        Credentials: !GetAtt ApiToSqsRole.Arn
        RequestParameters:
          integration.request.header.Content-Type: "'application/x-www-form-urlencoded'"
        RequestTemplates:
          application/json: "Action=SendMessage&MessageBody=$input.body"
        IntegrationResponses:
          - StatusCode: 202
            ResponseTemplates:
              application/json: '{"message": "accepted"}'
      MethodResponses:
        - StatusCode: 202

  AlertApiDeployment:
    Type: AWS::ApiGateway::Deployment
    DependsOn: AlertPostMethod
    Properties:
      RestApiId: !Ref AlertApi

  AlertApiStage:
    Type: AWS::ApiGateway::Stage
    Properties:
      RestApiId: !Ref AlertApi
      DeploymentId: !Ref AlertApiDeployment
      StageName: prod
      TracingEnabled: true

  # ============================================================
  # X-Ray サンプリングルール
  # ============================================================
  XRaySamplingRule:
    Type: AWS::XRay::SamplingRule
    Properties:
      SamplingRule:
        RuleName: alert-mailer-all
        Priority: 100
        FixedRate: 1.0
        ReservoirSize: 0
        ServiceName: alert-mailer
        ServiceType: "*"
        Host: "*"
        HTTPMethod: "*"
        URLPath: "*"
        ResourceARN: "*"
        Version: 1

Outputs:
  ApiEndpoint:
    Value: !Sub "https://${AlertApi}.execute-api.${AWS::Region}.amazonaws.com/prod/alert"
  QueueUrl:
    Value: !Ref AlertQueue
  DLQUrl:
    Value: !Ref AlertDLQ
  FunctionArn:
    Value: !GetAtt AlertMailerFunction.Arn
  CommonLayerArn:
    Value: !Ref CommonLayer
    Description: "他のLambdaでも使用可能な共通LayerのARN"
```

---

## 7. デプロイ手順

### deploy.sh

```bash
#!/bin/bash
set -euo pipefail

STACK_NAME="alert-mailer"
S3_BUCKET="your-deploy-bucket"
REGION="ap-northeast-1"

# Layer zip作成
cd layers/lambda_common
zip -r ../../lambda-common-layer.zip python/
cd ../..

# Function zip作成
cd functions/alert_mailer
zip -r ../../alert-mailer.zip .
cd ../..

# S3アップロード
aws s3 cp lambda-common-layer.zip s3://${S3_BUCKET}/lambda/lambda-common-layer.zip
aws s3 cp alert-mailer.zip s3://${S3_BUCKET}/lambda/alert-mailer.zip

# CloudFormation デプロイ
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name ${STACK_NAME} \
  --region ${REGION} \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    LambdaCodeS3Bucket=${S3_BUCKET} \
    MailFrom="alert@example.com" \
    MailTo="ops-team@example.com"
```

---

## 8. テスト仕様・テストコード

### 8.1 conftest.py（共通fixture）

```python
import os
import pytest
import json

# テスト時はX-Rayを無効化
os.environ["AWS_XRAY_SDK_ENABLED"] = "false"
os.environ["AWS_DEFAULT_REGION"] = "ap-northeast-1"
os.environ["AWS_REGION"] = "ap-northeast-1"
os.environ["MAIL_FROM"] = "test-from@example.com"
os.environ["MAIL_TO"] = "test-to@example.com"
os.environ["MAIL_CC"] = ""
os.environ["MAIL_BCC"] = ""
os.environ["MAIL_SUBJECT_PREFIX"] = "[TEST] "
os.environ["ENV_NAME"] = "テスト環境"
os.environ["FACILITY_NAME"] = "test-facility"
os.environ["NOTIFY_DESCRIPTION"] = "テスト通知定義"


@pytest.fixture
def mock_context():
    """Lambdaのcontextオブジェクトモック"""
    class Context:
        aws_request_id = "test-request-id-12345"
        function_name = "alert-mailer"
        memory_limit_in_mb = 256
        invoked_function_arn = "arn:aws:lambda:ap-northeast-1:123456789012:function:alert-mailer"
    return Context()


@pytest.fixture
def cloudwatch_alarm_event():
    """CloudWatch Alarm State Change テストイベント"""
    return {
        "source": "aws.cloudwatch",
        "detail-type": "CloudWatch Alarm State Change",
        "time": "2026-02-14T01:00:00Z",
        "region": "ap-northeast-1",
        "detail": {
            "alarmName": "test-cpu-alarm",
            "state": {
                "value": "ALARM",
                "reason": "Threshold Crossed: 1 out of 1 datapoints were greater than 80.0",
                "reasonData": '{"version":"1.0","queryDate":"2026-02-14T01:00:00Z"}'
            }
        }
    }


@pytest.fixture
def cloudwatch_logs_event():
    """CloudWatch Logs Subscription Filter テストイベント（base64+gzip済み）"""
    import base64
    import zlib
    raw = json.dumps({
        "logGroup": "/ecs/my-app",
        "logStream": "ecs/container/abc123",
        "logEvents": [
            {"timestamp": 1707872400000, "message": "ERROR: Connection timeout"},
            {"timestamp": 1707872401000, "message": "ERROR: Retry failed"},
        ]
    })
    compressed = zlib.compress(raw.encode(), wbits=16 + zlib.MAX_WBITS)
    encoded = base64.b64encode(compressed).decode()
    return {"awslogs": {"data": encoded}}


@pytest.fixture
def ecs_task_event():
    """ECS Task State Change テストイベント（失敗コンテナあり）"""
    return {
        "source": "aws.ecs",
        "detail-type": "ECS Task State Change",
        "time": "2026-02-14T02:00:00Z",
        "region": "ap-northeast-1",
        "detail": {
            "clusterArn": "arn:aws:ecs:ap-northeast-1:123456789012:cluster/my-cluster",
            "taskArn": "arn:aws:ecs:ap-northeast-1:123456789012:task/my-cluster/abc123",
            "taskDefinitionArn": "arn:aws:ecs:ap-northeast-1:123456789012:task-definition/my-task:1",
            "stoppedReason": "Essential container in task exited",
            "containers": [
                {"name": "app", "exitCode": 1, "reason": "OutOfMemory"},
                {"name": "sidecar", "exitCode": 0, "reason": ""}
            ]
        }
    }


@pytest.fixture
def ecs_task_event_no_failure():
    """ECS Task State Change テストイベント（失敗コンテナなし）"""
    return {
        "source": "aws.ecs",
        "detail-type": "ECS Task State Change",
        "time": "2026-02-14T02:00:00Z",
        "region": "ap-northeast-1",
        "detail": {
            "clusterArn": "arn:aws:ecs:ap-northeast-1:123456789012:cluster/my-cluster",
            "taskArn": "arn:aws:ecs:ap-northeast-1:123456789012:task/my-cluster/abc123",
            "taskDefinitionArn": "arn:aws:ecs:ap-northeast-1:123456789012:task-definition/my-task:1",
            "stoppedReason": "Scaling activity",
            "containers": [
                {"name": "app", "exitCode": 0},
                {"name": "sidecar", "exitCode": 0}
            ]
        }
    }


@pytest.fixture
def sqs_wrapped_event(cloudwatch_alarm_event):
    """SQSラップされたイベント"""
    return {
        "Records": [
            {"messageId": "msg-001", "body": json.dumps(cloudwatch_alarm_event)}
        ]
    }


@pytest.fixture
def field_map():
    return json.load(open("functions/alert_mailer/mappings/field_map.json"))


@pytest.fixture
def priority_map():
    return json.load(open("functions/alert_mailer/mappings/priority_map.json"))
```

### 8.2 test_handler.py

```python
import json
import pytest
from unittest.mock import patch, MagicMock
from functions.alert_mailer.handler import classify


class TestClassify:
    def test_cloudwatch_logs(self, cloudwatch_logs_event):
        assert classify(cloudwatch_logs_event) == "cloudwatch_logs"

    def test_cloudwatch_alarm(self, cloudwatch_alarm_event):
        assert classify(cloudwatch_alarm_event) == "cloudwatch_alarm"

    def test_ecs_task(self, ecs_task_event):
        assert classify(ecs_task_event) == "ecs_task"

    def test_unsupported_event_raises(self):
        with pytest.raises(ValueError, match="Unsupported event"):
            classify({"detail-type": "Unknown", "source": "aws.unknown"})


class TestLambdaHandler:
    @patch("functions.alert_mailer.handler.sender")
    @patch("functions.alert_mailer.handler.renderer")
    @patch("functions.alert_mailer.handler.extract_alarm")
    def test_sqs_unwrap_dispatch_render_send(
        self, mock_extract, mock_renderer, mock_sender,
        sqs_wrapped_event, mock_context
    ):
        """SQSアンラップ→抽出→レンダリング→送信の正常フロー"""
        mock_extract.return_value = {"priority": "ALARM", "monitor_id": "test"}
        mock_renderer.render.return_value = ("subject", "text", "<html>")

        from functions.alert_mailer.handler import lambda_handler
        lambda_handler(sqs_wrapped_event, mock_context)

        mock_extract.assert_called_once()
        mock_renderer.render.assert_called_once()
        mock_sender.send.assert_called_once_with("subject", "text", "<html>")

    @patch("functions.alert_mailer.handler.sender")
    @patch("functions.alert_mailer.handler.extract_ecs")
    def test_skip_when_extract_returns_none(self, mock_extract, mock_sender, mock_context):
        """extract()がNone返却時は送信スキップ"""
        mock_extract.return_value = None
        event = {
            "Records": [{"body": json.dumps({
                "source": "aws.ecs",
                "detail-type": "ECS Task State Change",
                "detail": {"containers": [{"exitCode": 0}]}
            })}]
        }
        from functions.alert_mailer.handler import lambda_handler
        lambda_handler(event, mock_context)
        mock_sender.send.assert_not_called()
```

### 8.3 test_cloudwatch_alarm.py

```python
import pytest
from unittest.mock import patch, MagicMock


class TestCloudWatchAlarmExtract:
    @patch("functions.alert_mailer.handlers.cloudwatch_alarm.cw")
    def test_metric_alarm_normal(self, mock_cw, cloudwatch_alarm_event, mock_context):
        """単一Metricアラーム正常抽出"""
        mock_cw.describe_alarms.return_value = {
            "MetricAlarms": [{
                "AlarmName": "test-cpu-alarm",
                "Namespace": "AWS/EC2",
                "MetricName": "CPUUtilization",
                "AlarmDescription": "CPU使用率アラーム",
                "Dimensions": [{"Name": "InstanceId", "Value": "i-12345"}],
            }],
            "CompositeAlarms": []
        }
        from functions.alert_mailer.handlers.cloudwatch_alarm import extract
        result = extract(cloudwatch_alarm_event, mock_context)

        assert result["priority"] == "ALARM"
        assert result["monitor_id"] == "test-cpu-alarm"
        assert result["monitor_detail"] == "AWS/EC2/CPUUtilization"
        assert result["scope"] == "InstanceId=i-12345"
        assert result["notify_uuid"] == "test-request-id-12345"

    @patch("functions.alert_mailer.handlers.cloudwatch_alarm.cw")
    def test_composite_alarm_same_handler(self, mock_cw, mock_context):
        """複合アラームも同じextractで処理可能"""
        event = {
            "source": "aws.cloudwatch",
            "detail-type": "CloudWatch Alarm State Change",
            "time": "2026-02-14T01:00:00Z",
            "region": "ap-northeast-1",
            "detail": {
                "alarmName": "composite-ecs-alarm",
                "state": {"value": "ALARM", "reason": "child alarm triggered"}
            }
        }
        mock_cw.describe_alarms.return_value = {
            "MetricAlarms": [],
            "CompositeAlarms": [{
                "AlarmName": "composite-ecs-alarm",
                "AlarmDescription": "ECS片系停止検知",
                "AlarmRule": "ALARM(task-a) OR ALARM(task-b)",
            }]
        }
        from functions.alert_mailer.handlers.cloudwatch_alarm import extract
        result = extract(event, mock_context)

        assert result["monitor_id"] == "composite-ecs-alarm"
        assert result["plugin_name"] == "CloudWatch Alarm"

    @patch("functions.alert_mailer.handlers.cloudwatch_alarm.cw")
    def test_describe_alarms_failure_fallback(self, mock_cw, cloudwatch_alarm_event, mock_context):
        """DescribeAlarms失敗時にevent情報だけでfallback"""
        mock_cw.describe_alarms.side_effect = Exception("AccessDenied")

        from functions.alert_mailer.handlers.cloudwatch_alarm import extract
        result = extract(cloudwatch_alarm_event, mock_context)

        assert result["monitor_id"] == "test-cpu-alarm"
        assert "Threshold Crossed" in result["message"]
        # DescribeAlarms失敗でもNoneにならない
        assert result is not None
```

### 8.4 test_cloudwatch_logs.py

```python
import pytest
import base64
import zlib
import json


class TestCloudWatchLogsExtract:
    def test_normal_extraction(self, cloudwatch_logs_event, mock_context):
        """正常抽出（2件のlogEvents）"""
        from functions.alert_mailer.handlers.cloudwatch_logs import extract
        result = extract(cloudwatch_logs_event, mock_context)

        assert result["priority"] == "ALARM"
        assert result["monitor_id"] == "/ecs/my-app"
        assert result["monitor_detail"] == "ecs/container/abc123"
        assert "Connection timeout" in result["message"]

    def test_multiple_log_events_in_org_message(self, cloudwatch_logs_event, mock_context):
        """org_messageに全件含まれること"""
        from functions.alert_mailer.handlers.cloudwatch_logs import extract
        result = extract(cloudwatch_logs_event, mock_context)

        assert "Connection timeout" in result["org_message"]
        assert "Retry failed" in result["org_message"]

    def test_truncation_over_5_events(self, mock_context):
        """6件以上のlogEventsで先頭5件+残件数になること"""
        raw = json.dumps({
            "logGroup": "/ecs/app",
            "logStream": "stream",
            "logEvents": [
                {"timestamp": 1707872400000, "message": f"ERROR line {i}"}
                for i in range(8)
            ]
        })
        compressed = zlib.compress(raw.encode(), wbits=16 + zlib.MAX_WBITS)
        event = {"awslogs": {"data": base64.b64encode(compressed).decode()}}

        from functions.alert_mailer.handlers.cloudwatch_logs import extract
        result = extract(event, mock_context)

        assert "他 3 件" in result["message"]
        assert "ERROR line 7" in result["org_message"]
```

### 8.5 test_ecs_task.py

```python
import pytest


class TestEcsTaskExtract:
    def test_failed_container(self, ecs_task_event, mock_context):
        """失敗コンテナありの正常抽出"""
        from functions.alert_mailer.handlers.ecs_task import extract
        result = extract(ecs_task_event, mock_context)

        assert result["priority"] == "TASK_STOPPED"
        assert result["monitor_id"] == "my-task:1"
        assert result["scope"] == "my-cluster"
        assert "OutOfMemory" in result["org_message"]
        assert "console.aws.amazon.com" in result["org_message"]

    def test_no_failure_returns_none(self, ecs_task_event_no_failure, mock_context):
        """失敗コンテナなしはNone"""
        from functions.alert_mailer.handlers.ecs_task import extract
        assert extract(ecs_task_event_no_failure, mock_context) is None

    def test_multiple_failed_containers(self, mock_context):
        """複数の失敗コンテナ"""
        event = {
            "source": "aws.ecs",
            "detail-type": "ECS Task State Change",
            "time": "2026-02-14T02:00:00Z",
            "detail": {
                "clusterArn": "arn:aws:ecs:ap-northeast-1:123456789012:cluster/cl",
                "taskArn": "arn:aws:ecs:ap-northeast-1:123456789012:task/cl/t1",
                "taskDefinitionArn": "arn:aws:ecs:ap-northeast-1:123456789012:task-definition/td:1",
                "stoppedReason": "multiple failures",
                "containers": [
                    {"name": "app1", "exitCode": 1, "reason": "OOM"},
                    {"name": "app2", "exitCode": 137, "reason": "SIGKILL"},
                ]
            }
        }
        from functions.alert_mailer.handlers.ecs_task import extract
        result = extract(event, mock_context)

        assert "app1" in result["org_message"]
        assert "app2" in result["org_message"]
        assert "SIGKILL" in result["org_message"]
```

### 8.6 test_renderer.py

```python
import pytest


class TestRenderer:
    def test_standard_style_html(self, field_map, priority_map):
        """統一スタイルのHTML生成"""
        from functions.alert_mailer.renderer import render
        fields = {
            "priority": "ALARM",
            "env_name": "テスト環境",
            "plugin_name": "CloudWatch Alarm",
            "monitor_id": "test-alarm",
            **{f["key"]: "-" for f in field_map["fields"]}
        }
        subject, body_text, body_html = render(fields, field_map, priority_map)

        assert "#0f1c50" in body_html    # ヘッダ色
        assert "#6785c1" in body_html    # 行ヘッダ色
        assert 'class="Critical"' in body_html
        assert "危険" in subject

    def test_warning_css_class(self, field_map, priority_map):
        """TASK_STOPPEDでWarningクラス適用"""
        from functions.alert_mailer.renderer import render
        fields = {
            "priority": "TASK_STOPPED",
            "env_name": "テスト",
            "plugin_name": "ECS",
            "monitor_id": "td",
            **{f["key"]: "-" for f in field_map["fields"]}
        }
        _, _, body_html = render(fields, field_map, priority_map)
        assert 'class="Warning"' in body_html

    def test_html_escape_xss(self, field_map, priority_map):
        """特殊文字がエスケープされること"""
        from functions.alert_mailer.renderer import render
        fields = {
            "priority": "ALARM",
            "env_name": "テスト",
            "plugin_name": "Test",
            "monitor_id": "test",
            "message": '<script>alert("xss")</script>',
            **{f["key"]: "-" for f in field_map["fields"]}
        }
        _, _, body_html = render(fields, field_map, priority_map)
        assert "<script>" not in body_html
        assert "&lt;script&gt;" in body_html

    def test_plain_text_generation(self, field_map, priority_map):
        """プレーンテキスト版が全項目含むこと"""
        from functions.alert_mailer.renderer import render
        fields = {
            "priority": "OK",
            "env_name": "テスト",
            "plugin_name": "Test",
            "monitor_id": "test",
            **{f["key"]: "-" for f in field_map["fields"]}
        }
        _, body_text, _ = render(fields, field_map, priority_map)
        assert "環境:" in body_text
        assert "重要度:" in body_text
```

### 8.7 test_sender.py

```python
import pytest
from unittest.mock import patch


class TestSender:
    @patch("functions.alert_mailer.sender.ses")
    def test_send_normal(self, mock_ses):
        """正しいパラメータでSES送信"""
        from functions.alert_mailer.sender import send
        send("テスト件名", "テスト本文", "<html>テスト</html>")

        mock_ses.send_email.assert_called_once()
        args = mock_ses.send_email.call_args[1]
        assert "[TEST] テスト件名" in args["Message"]["Subject"]["Data"]
        assert args["Destination"]["ToAddresses"] == ["test-to@example.com"]

    def test_split_empty(self):
        from functions.alert_mailer.sender import _split
        assert _split("") == []

    def test_split_comma(self):
        from functions.alert_mailer.sender import _split
        assert _split("a@t.com, b@t.com") == ["a@t.com", "b@t.com"]

    def test_split_semicolon(self):
        from functions.alert_mailer.sender import _split
        assert _split("a@t.com;b@t.com") == ["a@t.com", "b@t.com"]
```

### 8.8 test_decorator.py（Lambda共通Layer）

```python
import json
import pytest
from unittest.mock import patch, MagicMock


class TestLambdaBootstrap:
    @patch("lambda_common.tracer.init_tracer")
    @patch("lambda_common.logger.get_logger")
    def test_start_end_log_on_success(self, mock_get_logger, mock_tracer, mock_context):
        """正常終了時にSTART→ENDログが出ること"""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        from lambda_common.decorator import lambda_bootstrap

        @lambda_bootstrap(service_name="test-svc")
        def handler(event, context, logger=None):
            return {"statusCode": 200}

        handler({"Records": []}, mock_context)

        assert mock_logger.info.call_count == 2
        start = json.loads(mock_logger.info.call_args_list[0][0][0])
        end = json.loads(mock_logger.info.call_args_list[1][0][0])
        assert start["phase"] == "START"
        assert start["service"] == "test-svc"
        assert start["request_id"] == "test-request-id-12345"
        assert end["phase"] == "END"
        assert end["status"] == "SUCCESS"

    @patch("lambda_common.tracer.init_tracer")
    @patch("lambda_common.logger.get_logger")
    def test_error_log_and_reraise(self, mock_get_logger, mock_tracer, mock_context):
        """異常時にERRORログ出力→例外再raise"""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        from lambda_common.decorator import lambda_bootstrap

        @lambda_bootstrap(service_name="test-svc")
        def bad_handler(event, context, logger=None):
            raise RuntimeError("test error")

        with pytest.raises(RuntimeError, match="test error"):
            bad_handler({}, mock_context)

        mock_logger.error.assert_called_once()
        err = json.loads(mock_logger.error.call_args[0][0])
        assert err["phase"] == "ERROR"
        assert err["status"] == "FAILURE"
        assert err["error_type"] == "RuntimeError"
        assert "test error" in err["error_message"]
        assert "Traceback" in err["stacktrace"]

    @patch("lambda_common.tracer.init_tracer")
    @patch("lambda_common.logger.get_logger")
    def test_logger_passed_to_handler(self, mock_get_logger, mock_tracer, mock_context):
        """handlerにloggerが渡されること"""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        from lambda_common.decorator import lambda_bootstrap

        received_logger = None

        @lambda_bootstrap(service_name="test-svc")
        def handler(event, context, logger=None):
            nonlocal received_logger
            received_logger = logger

        handler({}, mock_context)
        assert received_logger is mock_logger
```

### 8.9 test_logger.py（Lambda共通Layer）

```python
import json
import pytest
from unittest.mock import MagicMock


class TestLogStart:
    def test_sqs_event(self):
        from lambda_common.logger import log_start
        mock = MagicMock()
        log_start(mock, "svc", "req-001", {"Records": [1, 2, 3]})
        parsed = json.loads(mock.info.call_args[0][0])
        assert parsed["phase"] == "START"
        assert parsed["service"] == "svc"
        assert parsed["request_id"] == "req-001"
        assert "SQS records=3" in parsed["event_summary"]

    def test_cloudwatch_logs_event(self):
        from lambda_common.logger import log_start
        mock = MagicMock()
        log_start(mock, "svc", "req-001", {"awslogs": {}})
        parsed = json.loads(mock.info.call_args[0][0])
        assert parsed["event_summary"] == "CloudWatch Logs subscription"

    def test_detail_type_event(self):
        from lambda_common.logger import log_start
        mock = MagicMock()
        log_start(mock, "svc", "req-001", {"detail-type": "ECS Task State Change"})
        parsed = json.loads(mock.info.call_args[0][0])
        assert parsed["event_summary"] == "ECS Task State Change"


class TestLogEnd:
    def test_success_fields(self):
        from lambda_common.logger import log_end
        mock = MagicMock()
        log_end(mock, "svc", "req-001")
        parsed = json.loads(mock.info.call_args[0][0])
        assert parsed["phase"] == "END"
        assert parsed["status"] == "SUCCESS"
        assert parsed["service"] == "svc"


class TestLogError:
    def test_error_with_stacktrace(self):
        from lambda_common.logger import log_error
        mock = MagicMock()
        try:
            raise ValueError("broken")
        except ValueError as e:
            log_error(mock, "svc", "req-001", e)
        parsed = json.loads(mock.error.call_args[0][0])
        assert parsed["phase"] == "ERROR"
        assert parsed["status"] == "FAILURE"
        assert parsed["error_type"] == "ValueError"
        assert "broken" in parsed["error_message"]
        assert "Traceback" in parsed["stacktrace"]


class TestLogWarn:
    def test_warn_message(self):
        from lambda_common.logger import log_warn
        mock = MagicMock()
        log_warn(mock, "svc", "req-001", "skipping bad record")
        parsed = json.loads(mock.warning.call_args[0][0])
        assert parsed["phase"] == "WARN"
        assert parsed["message"] == "skipping bad record"
```

---

## 9. 設計方針・注意事項

### 9.1 複合アラームの扱い
- Lambda側で子アラーム展開は行わない
- CloudWatch側のActionsSuppressorで発報制御する前提
- DescribeAlarmsは1回（exactマッチ）のみ
- 複合アラームも単一アラームも同じextract処理

### 9.2 CloudWatch Logsの複数logEvents
- 全件ループに変更（現行は logEvents[0] のみ）
- message: 先頭5件 + 残件数
- org_message: 全件の生データ

### 9.3 エラーハンドリング
- sender.send() 失敗: 例外をそのまま上げる（SQSリトライ）
- extract() 失敗: handler.pyでcatch → log_warn → raise（SQSリトライ）
- 未捕捉例外: decorator.pyでlog_error → raise（SQSリトライ）

### 9.4 Lambda共通Layerの横展開
- 新しいLambda追加時は `@lambda_bootstrap("service-name")` の1行だけで統一ログ・X-Rayが入る
- CommonLayerArnをOutputsに出力しているので、他のCloudFormationスタックからも参照可能
- ログフォーマットが全Lambdaで統一されるため、CloudWatch Logs Insightsでの横断検索が容易

---

## 10. フェーズ分け

### Phase 1（本タスク）
- モジュール分割（handler/handlers/renderer/sender/utils/mappings）
- デザイン統一（field_map.json + priority_map.json + HTMLテンプレート）
- 単一アラーム + CloudWatch Logs + ECS Taskの3パターン
- 複合アラームは単一と同じ処理（シンプル化）
- Lambda共通Layer（decorator/logger/tracer/config）
- ユニットテスト（pytest、カバレッジ90%以上目標）
- CloudFormationテンプレート

### Phase 2（後続）
- API Gateway → SQS → Lambda構成への移行
- SQSによる流量制御（BatchSize=1, MaximumConcurrency=1, sleep(1)）
- X-Rayトレーシング有効化・動作確認
- DLQの監視アラーム追加

### Phase 3（後続）
- コンテナ片系停止の検知をEventBridge（ECS Task State Change）で代替検証
- CloudWatch側の複合アラーム設定見直し（ActionsSuppressor活用）
- CI/CDパイプライン構築（GitHub Actions or CodePipeline）