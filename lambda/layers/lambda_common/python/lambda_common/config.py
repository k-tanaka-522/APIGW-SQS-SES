"""
config.py - 共通設定ロード

field_map.json の common セクションに対応する値を環境変数から取得する。
この共通フィールドは全イベント種別で共通してメール本文に含まれる。

対応する環境変数:
  - ENV_NAME: 環境表示名（例: "本番環境"）
  - AWS_ACCOUNT_ID: AWS アカウントID
  - AWS_REGION: AWS リージョン
  - FACILITY_NAME: ファシリティ識別子（例: "aws-production"）
  - NOTIFY_DESCRIPTION: 通知定義ラベル
"""
import os


def load_common_fields() -> dict:
    """
    環境変数から共通フィールドを辞書として返す。

    Returns:
        field_map.json の common セクションのキーに対応する辞書
        - env_name: 環境表示名
        - facility_id: "アカウントID-リージョン" 形式の識別子
        - facility_name: ファシリティ名
        - notify_description: 通知定義の説明
    """
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    account_id = os.environ.get("AWS_ACCOUNT_ID", "-")
    return {
        "env_name": os.environ.get("ENV_NAME", "-"),
        "facility_id": f"{account_id}-{region}",
        "facility_name": os.environ.get("FACILITY_NAME", "-"),
        "notify_description": os.environ.get("NOTIFY_DESCRIPTION", "-"),
    }
