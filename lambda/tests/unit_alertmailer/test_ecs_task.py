import pytest


class TestEcsTaskExtract:
    def test_failed_container(self, ecs_task_event, mock_context):
        """失敗コンテナありの正常抽出"""
        from handlers.ecs_task import extract
        result = extract(ecs_task_event, mock_context)

        assert result["priority"] == "TASK_STOPPED"
        assert result["monitor_id"] == "my-task:1"
        assert result["scope"] == "my-cluster"
        assert "OutOfMemory" in result["org_message"]
        assert "console.aws.amazon.com" in result["org_message"]

    def test_no_failure_returns_none(self, ecs_task_event_no_failure, mock_context):
        """失敗コンテナなしはNone"""
        from handlers.ecs_task import extract
        assert extract(ecs_task_event_no_failure, mock_context) is None

    def test_multiple_failed_containers(self, mock_context):
        """複数の失敗コンテナ"""
        event = {
            "source": "aws.ecs",
            "detail-type": "ECS Task State Change",
            "time": "2026-02-14T02:00:00Z",
            "detail": {
                "clusterArn": "arn:aws:ecs:ap-northeast-1:123456789012:cluster/cl",
                "taskArn": "arn:aws:ecs:ap-northeast-1:123456789012:task/cl/t1",
                "taskDefinitionArn": "arn:aws:ecs:ap-northeast-1:123456789012:task-definition/td:1",
                "stoppedReason": "multiple failures",
                "containers": [
                    {"name": "app1", "exitCode": 1, "reason": "OOM"},
                    {"name": "app2", "exitCode": 137, "reason": "SIGKILL"},
                ]
            }
        }
        from handlers.ecs_task import extract
        result = extract(event, mock_context)

        assert "app1" in result["org_message"]
        assert "app2" in result["org_message"]
        assert "SIGKILL" in result["org_message"]
