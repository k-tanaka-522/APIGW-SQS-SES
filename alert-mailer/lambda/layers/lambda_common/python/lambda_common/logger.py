"""
logger.py - 構造化ログ出力

Lambda 共通の構造化ログ（JSON形式）を提供する。
CloudWatch Logs Insights での横断検索を容易にするため、
全 Lambda で統一されたフォーマットでログを出力する。

ログフェーズ:
  - START: Lambda 実行開始
  - END: Lambda 正常終了
  - WARN: 個別レコード処理の警告（処理続行時）
  - ERROR: Lambda 異常終了（スタックトレース付き）
"""
import logging
import json
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# タイムスタンプ出力用の日本標準時
JST = ZoneInfo("Asia/Tokyo")


def get_logger(service_name: str) -> logging.Logger:
    """サービス名を名前空間とするロガーを取得する。"""
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    return logger


def _now_jst() -> str:
    """現在時刻をJSTのISO 8601形式で返す（ログのタイムスタンプ用）。"""
    return datetime.now(timezone.utc).astimezone(JST).isoformat()


def _summarize_event(event: dict) -> str:
    """
    イベントの要約文字列を生成する。

    イベント全体をログに出力するとサイズが大きくなるため、
    イベント種別に応じた短い要約に変換する。
    """
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
    """Lambda 実行開始ログを JSON 形式で出力する。"""
    logger.info(json.dumps({
        "phase": "START",
        "service": service_name,
        "request_id": request_id,
        "timestamp": _now_jst(),
        "event_summary": _summarize_event(event),
    }, ensure_ascii=False))


def log_end(logger: logging.Logger, service_name: str,
            request_id: str) -> None:
    """Lambda 正常終了ログを JSON 形式で出力する。"""
    logger.info(json.dumps({
        "phase": "END",
        "service": service_name,
        "request_id": request_id,
        "timestamp": _now_jst(),
        "status": "SUCCESS",
    }, ensure_ascii=False))


def log_error(logger: logging.Logger, service_name: str,
              request_id: str, error: Exception) -> None:
    """Lambda 異常終了ログを JSON 形式で出力する（スタックトレース付き）。"""
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
    """個別レコード処理の警告ログを JSON 形式で出力する（処理続行時に使用）。"""
    logger.warning(json.dumps({
        "phase": "WARN",
        "service": service_name,
        "request_id": request_id,
        "timestamp": _now_jst(),
        "message": message,
    }, ensure_ascii=False))
