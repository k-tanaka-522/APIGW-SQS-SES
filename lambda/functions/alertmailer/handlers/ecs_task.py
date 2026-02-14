"""
ecs_task.py - ECS Task State Change イベントハンドラ

EventBridge 経由で受信した ECS タスク状態変更イベントから
通知メールに必要なフィールドを抽出する。

設計方針:
  - 失敗コンテナ（exitCode != 0 または reason あり）が存在する場合のみ通知
  - 失敗コンテナがない場合は None を返して送信をスキップ
  - ECS コンソールへの直リンクを org_message に含める
"""
import os
from utils import format_jst
from datetime import datetime, timezone


def extract(event: dict, context) -> dict | None:
    """
    ECS Task State Change イベントからフィールドを抽出する。

    Args:
        event: EventBridge から受信した ECS タスクイベント
        context: Lambda コンテキスト

    Returns:
        通知メールのフィールド辞書。失敗コンテナがなければ None（送信スキップ）
    """
    detail = event.get("detail", {})
    containers = detail.get("containers", [])

    # 失敗コンテナの抽出: exitCode が 0/None 以外、または reason が設定されているもの
    failed = [
        c for c in containers
        if c.get("exitCode") not in (0, None) or c.get("reason")
    ]

    # 失敗コンテナがなければ通知不要（正常終了やスケーリングによる停止等）
    if not failed:
        return None

    # --- ARN からリソース名を抽出 ---
    cluster_arn = detail.get("clusterArn", "")
    task_arn = detail.get("taskArn", "")
    task_def_arn = detail.get("taskDefinitionArn", "")
    stopped_reason = detail.get("stoppedReason", "")

    # ARN の末尾がリソース名（例: "arn:.../cluster/my-cluster" → "my-cluster"）
    cluster_name = cluster_arn.split("/")[-1] if cluster_arn else ""
    task_id = task_arn.split("/")[-1] if task_arn else ""
    task_def_name = task_def_arn.split("/")[-1] if task_def_arn else ""

    # イベント発生時刻をパース
    event_time = event.get("time", "")
    try:
        dt_utc = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    except Exception:
        dt_utc = datetime.now(timezone.utc)

    # --- 失敗コンテナの詳細情報を組み立て ---
    failed_lines = []
    for c in failed:
        failed_lines.append(
            f"Container: {c.get('name')} / "
            f"exitCode: {c.get('exitCode')} / "
            f"reason: {c.get('reason', '')}"
        )
    failed_detail = "\n".join(failed_lines)

    # AWS コンソールの ECS タスク詳細ページへの直リンクを生成
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    ecs_url = ""
    if cluster_name and task_id:
        ecs_url = (
            f"https://{region}.console.aws.amazon.com/ecs/v2/clusters/"
            f"{cluster_name}/tasks/{task_id}/details?region={region}"
        )

    # オリジナルメッセージ = 失敗コンテナ詳細 + コンソールURL
    org_message = failed_detail
    if ecs_url:
        org_message += f"\n\nECS Task URL:\n{ecs_url}"

    return {
        "priority": "TASK_STOPPED",
        "msg_code": "ECS-TASK",
        "plugin_name": "ECS Task Monitor",
        "monitor_id": task_def_name,              # タスク定義名を監視項目IDに
        "monitor_detail": stopped_reason,          # 停止理由を監視詳細に
        "monitor_description": f"ECSタスク監視 ({task_def_name})",
        "scope": cluster_name,                     # クラスタ名をスコープに
        "generation_date": format_jst(dt_utc),
        "application": f"ECS ({cluster_name}/{task_def_name})",
        "message": stopped_reason,
        "org_message": org_message,
        "notify_uuid": context.aws_request_id,
    }
