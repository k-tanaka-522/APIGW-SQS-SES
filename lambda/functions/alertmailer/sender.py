"""
sender.py - SES メール送信

Amazon SES を使用して通知メールを送信する。
送信先・送信元は環境変数から取得する。

環境変数:
  - MAIL_FROM: 送信元アドレス（必須）
  - MAIL_TO: 送信先アドレス（カンマまたはセミコロン区切り、必須）
  - MAIL_CC: CC アドレス（カンマまたはセミコロン区切り、任意）
  - MAIL_BCC: BCC アドレス（カンマまたはセミコロン区切り、任意）
  - MAIL_SUBJECT_PREFIX: 件名プレフィックス（例: "[AWS監視] "）
"""
import boto3
import os

# SES クライアント（モジュールレベルで初期化してコールドスタートを最適化）
ses = boto3.client("ses", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))


def send(subject: str, body_text: str, body_html: str) -> None:
    """
    SES 経由でメールを送信する。

    Args:
        subject: メール件名（プレフィックスは本関数内で付与）
        body_text: プレーンテキスト本文
        body_html: HTML 本文
    """
    # 環境変数からメールアドレスを取得（MAIL_TO, MAIL_FROM は必須）
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
    """カンマまたはセミコロン区切りのアドレス文字列をリストに分割する。空文字列は空リストを返す。"""
    if not s:
        return []
    return [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]
