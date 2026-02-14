"""
tracer.py - AWS X-Ray トレーシング

X-Ray の初期化とサブセグメント作成のヘルパーを提供する。
init_tracer() を呼び出すと、boto3 の全サービス呼び出しが
自動的に X-Ray トレースの対象になる。

テスト時は環境変数 AWS_XRAY_SDK_ENABLED=false を設定することで無効化できる。
"""
from aws_xray_sdk.core import patch_all, xray_recorder


def init_tracer():
    """
    X-Ray を初期化し、boto3 の全サービス呼び出しを自動トレースする。

    lambda_bootstrap デコレータから呼び出される。
    """
    patch_all()


def trace_subsegment(name: str):
    """
    明示的にサブセグメントを作成するコンテキストマネージャを返す。

    特定の処理区間（例: DescribeAlarms API 呼び出し）を
    X-Ray のサブセグメントとして可視化したい場合に使用する。

    使い方:
        with trace_subsegment("describe_alarms"):
            resp = cw.describe_alarms(AlarmNames=[name])
    """
    return xray_recorder.in_subsegment(name)
