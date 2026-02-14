"""
logger.py - 構造化ログ出力

Lambda 共通の構造化ログ（タブ区切りプレーンテキスト形式）を提供する。
CloudWatch Logs での視認性を重視し、全 Lambda で統一されたフォーマットでログを出力する。

出力形式: phase service request_id timestamp ...
  - START: phase service request_id timestamp event_summary
  - END:   phase service request_id timestamp SUCCESS
  - WARN:  phase service request_id timestamp message
  - ERROR: phase service request_id timestamp FAILURE error_type error_message
           (次行以降にスタックトレース)
"""
import logging
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
    """Lambda 実行開始ログを出力する。"""
    logger.info(f"START {service_name} {request_id} {_now_jst()} {_summarize_event(event)}")


def log_end(logger: logging.Logger, service_name: str,
            request_id: str) -> None:
    """Lambda 正常終了ログを出力する。"""
    logger.info(f"END {service_name} {request_id} {_now_jst()} SUCCESS")


def log_error(logger: logging.Logger, service_name: str,
              request_id: str, error: Exception) -> None:
    """Lambda 異常終了ログを出力する（次行以降にスタックトレース）。"""
    logger.error(
        f"ERROR {service_name} {request_id} {_now_jst()} FAILURE {type(error).__name__} {error}\n{traceback.format_exc()}"
    )


def log_warn(logger: logging.Logger, service_name: str,
             request_id: str, message: str) -> None:
    """個別レコード処理の警告ログを出力する（処理続行時に使用）。"""
    logger.warning(f"WARN {service_name} {request_id} {_now_jst()} {message}")
