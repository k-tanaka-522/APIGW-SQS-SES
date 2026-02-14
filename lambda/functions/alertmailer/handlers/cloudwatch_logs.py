"""
cloudwatch_logs.py - CloudWatch Logs Subscription Filter イベントハンドラ

CloudWatch Logs のサブスクリプションフィルタ経由で受信したログイベントから
通知メールに必要なフィールドを抽出する。

処理方針:
  - logEvents は全件処理する
  - message フィールド: 先頭5件 + 残件数の要約を表示
  - org_message フィールド: 全件の生データを区切り文字付きで結合
"""
import base64
import json
import zlib
from utils import format_jst
from datetime import datetime, timezone


def extract(event: dict, context) -> dict:
    """
    CloudWatch Logs Subscription Filter イベントからフィールドを抽出する。

    イベントの awslogs.data は base64 + gzip で圧縮されており、
    デコード → 展開 → JSON パースの順で元データを復元する。

    Args:
        event: SQS ボディから取り出した CloudWatch Logs イベント
        context: Lambda コンテキスト

    Returns:
        通知メールのフィールド辞書（field_map.json のキーに対応）
    """
    # base64デコード → gzip展開 でログデータを復元
    data = zlib.decompress(
        base64.b64decode(event["awslogs"]["data"]),
        16 + zlib.MAX_WBITS  # gzip形式のウィンドウビット指定
    )
    data_json = json.loads(data)

    log_group = data_json.get("logGroup", "")
    log_stream = data_json.get("logStream", "")
    log_events = data_json.get("logEvents", [])

    # --- メッセージの要約と全文を作成 ---
    # 通知メール本文には先頭5件のみ表示し、残りは件数で省略
    messages = [e.get("message", "") for e in log_events]
    summary = "\n".join(messages[:5])
    if len(messages) > 5:
        summary += f"\n... 他 {len(messages) - 5} 件"

    # オリジナルメッセージには全件をセパレータ区切りで格納
    full_messages = "\n---\n".join(messages)

    # 先頭イベントのタイムスタンプ（ミリ秒UNIX時刻）をJSTに変換
    first_ts = log_events[0].get("timestamp", 0) if log_events else 0
    dt_utc = datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc)

    return {
        "priority": "ALARM",                                 # ログ検知は常に「危険」扱い
        "msg_code": "CW-LOGS",
        "plugin_name": "CloudWatch Logs",
        "monitor_id": log_group,                              # ロググループ名を監視項目IDに
        "monitor_detail": log_stream,                         # ログストリーム名を監視詳細に
        "monitor_description": f"ログ監視 ({log_group})",
        "scope": log_group,
        "generation_date": format_jst(dt_utc),
        "application": f"ログ監視 ({log_group})",
        "message": summary,                                   # 先頭5件の要約
        "org_message": full_messages,                         # 全件の生データ
        "notify_uuid": context.aws_request_id,
    }
