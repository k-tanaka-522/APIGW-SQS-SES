import os
import pytest
import json

# テスト時はX-Rayを無効化
os.environ["AWS_XRAY_SDK_ENABLED"] = "false"
os.environ["AWS_DEFAULT_REGION"] = "ap-northeast-1"
os.environ["AWS_REGION"] = "ap-northeast-1"
os.environ["MAIL_FROM"] = "test-from@example.com"
os.environ["MAIL_TO"] = "test-to@example.com"
os.environ["MAIL_CC"] = ""
os.environ["MAIL_BCC"] = ""
os.environ["MAIL_SUBJECT_PREFIX"] = "[TEST] "
os.environ["ENV_NAME"] = "テスト環境"
os.environ["FACILITY_NAME"] = "test-facility"
os.environ["NOTIFY_DESCRIPTION"] = "テスト通知定義"


@pytest.fixture
def mock_context():
    """Lambdaのcontextオブジェクトモック"""
    class Context:
        aws_request_id = "test-request-id-12345"
        function_name = "alert-mailer"
        memory_limit_in_mb = 256
        invoked_function_arn = "arn:aws:lambda:ap-northeast-1:123456789012:function:alert-mailer"
    return Context()


@pytest.fixture
def cloudwatch_alarm_event():
    """CloudWatch Alarm State Change テストイベント"""
    return {
        "source": "aws.cloudwatch",
        "detail-type": "CloudWatch Alarm State Change",
        "time": "2026-02-14T01:00:00Z",
        "region": "ap-northeast-1",
        "detail": {
            "alarmName": "test-cpu-alarm",
            "state": {
                "value": "ALARM",
                "reason": "Threshold Crossed: 1 out of 1 datapoints were greater than 80.0",
                "reasonData": '{"version":"1.0","queryDate":"2026-02-14T01:00:00Z"}'
            }
        }
    }


@pytest.fixture
def cloudwatch_logs_event():
    """CloudWatch Logs Subscription Filter テストイベント（base64+gzip済み）"""
    import base64
    import zlib
    raw = json.dumps({
        "logGroup": "/ecs/my-app",
        "logStream": "ecs/container/abc123",
        "logEvents": [
            {"timestamp": 1707872400000, "message": "ERROR: Connection timeout"},
            {"timestamp": 1707872401000, "message": "ERROR: Retry failed"},
        ]
    })
    compressed = zlib.compress(raw.encode(), wbits=16 + zlib.MAX_WBITS)
    encoded = base64.b64encode(compressed).decode()
    return {"awslogs": {"data": encoded}}


@pytest.fixture
def ecs_task_event():
    """ECS Task State Change テストイベント（失敗コンテナあり）"""
    return {
        "source": "aws.ecs",
        "detail-type": "ECS Task State Change",
        "time": "2026-02-14T02:00:00Z",
        "region": "ap-northeast-1",
        "detail": {
            "clusterArn": "arn:aws:ecs:ap-northeast-1:123456789012:cluster/my-cluster",
            "taskArn": "arn:aws:ecs:ap-northeast-1:123456789012:task/my-cluster/abc123",
            "taskDefinitionArn": "arn:aws:ecs:ap-northeast-1:123456789012:task-definition/my-task:1",
            "stoppedReason": "Essential container in task exited",
            "containers": [
                {"name": "app", "exitCode": 1, "reason": "OutOfMemory"},
                {"name": "sidecar", "exitCode": 0, "reason": ""}
            ]
        }
    }


@pytest.fixture
def ecs_task_event_no_failure():
    """ECS Task State Change テストイベント（失敗コンテナなし）"""
    return {
        "source": "aws.ecs",
        "detail-type": "ECS Task State Change",
        "time": "2026-02-14T02:00:00Z",
        "region": "ap-northeast-1",
        "detail": {
            "clusterArn": "arn:aws:ecs:ap-northeast-1:123456789012:cluster/my-cluster",
            "taskArn": "arn:aws:ecs:ap-northeast-1:123456789012:task/my-cluster/abc123",
            "taskDefinitionArn": "arn:aws:ecs:ap-northeast-1:123456789012:task-definition/my-task:1",
            "stoppedReason": "Scaling activity",
            "containers": [
                {"name": "app", "exitCode": 0},
                {"name": "sidecar", "exitCode": 0}
            ]
        }
    }


@pytest.fixture
def sqs_wrapped_event(cloudwatch_alarm_event):
    """SQSラップされたイベント"""
    return {
        "Records": [
            {"messageId": "msg-001", "body": json.dumps(cloudwatch_alarm_event)}
        ]
    }


@pytest.fixture
def field_map():
    from pathlib import Path
    map_path = Path(__file__).resolve().parent.parent / "lambda" / "alertmailer" / "mappings" / "field_map.json"
    with open(map_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def priority_map():
    from pathlib import Path
    map_path = Path(__file__).resolve().parent.parent / "lambda" / "alertmailer" / "mappings" / "priority_map.json"
    with open(map_path, encoding="utf-8") as f:
        return json.load(f)
