"""
handler.py - Lambda エントリポイント

SQS から受信したメッセージをアンラップし、イベント種別に応じた
ハンドラへディスパッチ → レンダリング → SES送信 の一連の処理を行う。

処理フロー:
  1. SQSレコードからボディを取り出す
  2. classify() でイベント種別を判定
  3. 対応する extract() で通知用フィールドを抽出
  4. renderer.render() で統一フォーマットの HTML/テキストを生成
  5. sender.send() で SES 経由でメール送信
"""
import json
import time
from pathlib import Path
from lambda_common.decorator import lambda_bootstrap
from lambda_common.config import load_common_fields
from handlers.cloudwatch_logs import extract as extract_logs
from handlers.cloudwatch_alarm import extract as extract_alarm
from handlers.ecs_task import extract as extract_ecs
import renderer
import sender

# --- モジュールレベル初期化（コールドスタート時に1回だけ実行） ---

# マッピング定義の読み込み（field_map: 表示項目定義、priority_map: 重要度→CSSクラス対応）
_HERE = Path(__file__).resolve().parent

with open(_HERE / "mappings" / "field_map.json", encoding="utf-8") as f:
    FIELD_MAP = json.load(f)
with open(_HERE / "mappings" / "priority_map.json", encoding="utf-8") as f:
    PRIORITY_MAP = json.load(f)

# 環境変数から取得する共通フィールド（環境名、ファシリティID等）
COMMON_FIELDS = load_common_fields()


@lambda_bootstrap(service_name="alert-mailer")
def lambda_handler(event, context, logger=None):
    """SQS イベントソースマッピングから呼び出されるエントリポイント。"""
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        try:
            _process(body, context, logger)
        except Exception as e:
            # 処理失敗時は警告ログを出力し、例外を再raiseしてSQSリトライに委ねる
            from lambda_common.logger import log_warn
            log_warn(logger, "alert-mailer", context.aws_request_id,
                     f"Record processing failed: {e}")
            raise  # SQSリトライに委ねる
        # SES の送信レート制限（秒間1通）を遵守するためのスリープ
        time.sleep(1)


def _process(body, context, logger):
    """個別レコードの処理: 種別判定 → フィールド抽出 → レンダリング → 送信。"""
    # イベント種別を判定し、対応する抽出関数を取得
    event_type = classify(body)
    dispatchers = {
        "cloudwatch_logs": extract_logs,
        "cloudwatch_alarm": extract_alarm,
        "ecs_task": extract_ecs,
    }
    extractor = dispatchers[event_type]

    # 各ハンドラがイベントから通知に必要なフィールドを抽出
    fields = extractor(body, context)

    # extract() が None を返した場合は通知不要（例: ECSタスクの正常終了）
    if fields is None:
        logger.info("extract returned None. Skipping.")
        return

    # 共通フィールド（環境名等）と個別フィールドをマージ
    fields = {**COMMON_FIELDS, **fields}

    # 統一フォーマットで件名・本文（テキスト/HTML）を生成し、SESで送信
    subject, body_text, body_html = renderer.render(fields, FIELD_MAP, PRIORITY_MAP)
    sender.send(subject, body_text, body_html)


def classify(event: dict) -> str:
    """
    イベント種別を判定して文字列キーを返す。

    判定ロジック:
      - "awslogs" キーがあれば → CloudWatch Logs Subscription Filter
      - detail-type + source で ECS Task / CloudWatch Alarm を判定
      - いずれにも該当しなければ ValueError を送出
    """
    if "awslogs" in event:
        return "cloudwatch_logs"
    detail_type = event.get("detail-type")
    source = event.get("source")
    if detail_type == "ECS Task State Change" and source == "aws.ecs":
        return "ecs_task"
    if detail_type == "CloudWatch Alarm State Change" and source == "aws.cloudwatch":
        return "cloudwatch_alarm"
    raise ValueError(f"Unsupported event: detail-type={detail_type}, source={source}")
