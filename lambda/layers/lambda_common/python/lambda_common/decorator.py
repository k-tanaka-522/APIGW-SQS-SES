"""
decorator.py - Lambda 共通デコレータ

@lambda_bootstrap デコレータを提供する。
すべての Lambda 関数にこのデコレータを適用することで、
構造化ログ・X-Ray トレーシング・エラーハンドリングが自動的に有効になる。

使い方:
    @lambda_bootstrap(service_name="alert-mailer")
    def lambda_handler(event, context, logger=None):
        ...
"""
from functools import wraps
from lambda_common.logger import get_logger, log_start, log_end, log_error
from lambda_common.tracer import init_tracer


def lambda_bootstrap(service_name: str):
    """
    1行でログ・X-Ray・エラーハンドリングを初期化するデコレータ。

    デコレータ適用時（モジュールロード時）に以下を実行:
      - サービス名付きロガーの生成
      - X-Ray トレーシングの初期化（patch_all）

    各呼び出し時に以下を自動実行:
      - START ログ出力
      - 正常終了時に END ログ出力
      - 例外発生時に ERROR ログ出力 → 例外を再raise（SQSリトライ用）
    """
    def decorator(func):
        # デコレータ適用時（コールドスタート時）に1回だけ実行
        logger = get_logger(service_name)
        init_tracer()

        @wraps(func)
        def wrapper(event, context):
            request_id = context.aws_request_id
            # Lambda 実行開始ログを出力
            log_start(logger, service_name, request_id, event)
            try:
                # 元の関数に logger を注入して呼び出し
                result = func(event, context, logger=logger)
                # 正常終了ログを出力
                log_end(logger, service_name, request_id)
                return result
            except Exception as e:
                # 異常終了ログを出力し、例外を再raiseしてSQSリトライに委ねる
                log_error(logger, service_name, request_id, e)
                raise
        return wrapper
    return decorator
