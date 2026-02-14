"""
cloudwatch_alarm.py - CloudWatch Alarm State Change イベントハンドラ

EventBridge 経由で受信した CloudWatch Alarm の状態変更イベントから
通知メールに必要なフィールドを抽出する。

設計方針:
  - 複合アラームも単一アラームも同じ処理で対応
  - CloudWatch 側の ActionsSuppressor による発報制御を前提とし、
    Lambda 側では子アラーム展開や多段フォールバックは行わない
  - DescribeAlarms は1回（exactマッチ）のみ。失敗時は event 情報で fallback
"""
import boto3
import logging
import os
from utils import format_jst
from datetime import datetime, timezone
from lambda_common.tracer import trace_subsegment

logger = logging.getLogger(__name__)

# CloudWatch クライアント（モジュールレベルで初期化してコールドスタートを最適化）
cw = boto3.client("cloudwatch",
                   region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))


def extract(event: dict, context) -> dict:
    """
    CloudWatch Alarm State Change イベントからフィールドを抽出する。

    Args:
        event: EventBridge から受信したアラームイベント
        context: Lambda コンテキスト（aws_request_id を通知UUIDに使用）

    Returns:
        通知メールのフィールド辞書（field_map.json のキーに対応）
    """
    detail = event.get("detail", {})
    alarm_name = detail.get("alarmName", "")
    state = detail.get("state", {})
    region = event.get("region", os.environ.get("AWS_REGION", "ap-northeast-1"))

    # イベント発生時刻をパース（ISO 8601形式、末尾Zをオフセット表記に変換）
    event_time = event.get("time", "")
    try:
        dt_utc = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    except Exception:
        # パース失敗時は現在時刻で代替
        dt_utc = datetime.now(timezone.utc)

    # --- DescribeAlarms でアラーム詳細を取得（1回のみ） ---
    # 失敗してもイベント情報だけで通知は可能なので、例外は握りつぶす
    namespace = ""
    metric_name = ""
    alarm_description = ""
    dims_str = "-"

    try:
        with trace_subsegment("describe_alarms"):
            resp = cw.describe_alarms(AlarmNames=[alarm_name])

        # 単一メトリクスアラームの場合
        if resp.get("MetricAlarms"):
            info = resp["MetricAlarms"][0]
            namespace = info.get("Namespace", "")
            metric_name = info.get("MetricName", "")
            alarm_description = info.get("AlarmDescription", "")
            # ディメンション情報を "Name=Value" 形式の文字列に変換
            dims = info.get("Dimensions", [])
            dims_str = ", ".join(f"{d['Name']}={d['Value']}" for d in dims) if dims else "-"

        # 複合アラームの場合
        elif resp.get("CompositeAlarms"):
            info = resp["CompositeAlarms"][0]
            alarm_description = info.get("AlarmDescription", "")
            namespace = "Composite"
            # 複合アラームではメトリクス名の代わりにルール式を使用
            metric_name = info.get("AlarmRule", "")
    except Exception:
        # DescribeAlarms 失敗時はイベント情報のみで構成（fallback）
        logger.warning("DescribeAlarms failed for %s, using event data only", alarm_name, exc_info=True)

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
        "message": state.get("reason", ""),          # アラーム遷移理由（人間向け説明文）
        "org_message": state.get("reasonData", ""),   # アラーム遷移理由（JSON生データ）
        "notify_uuid": context.aws_request_id,
    }
