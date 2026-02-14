"""
utils.py - ユーティリティ関数

日付フォーマット等、複数モジュールから共通で使用する補助関数を定義する。
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# 日本標準時（JST = UTC+9）
JST = ZoneInfo("Asia/Tokyo")


def format_jst(dt_utc: datetime) -> str:
    """
    UTC の datetime を JST に変換し、"YYYY/MM/DD HH:MM:SS" 形式の文字列で返す。

    タイムゾーン情報が付与されていない naive な datetime の場合は UTC として扱う。
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(JST).strftime("%Y/%m/%d %H:%M:%S")
