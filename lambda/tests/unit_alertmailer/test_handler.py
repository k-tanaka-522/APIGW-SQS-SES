import json
import pytest
from unittest.mock import patch, MagicMock
from handler import classify


class TestClassify:
    def test_cloudwatch_logs(self, cloudwatch_logs_event):
        assert classify(cloudwatch_logs_event) == "cloudwatch_logs"

    def test_cloudwatch_alarm(self, cloudwatch_alarm_event):
        assert classify(cloudwatch_alarm_event) == "cloudwatch_alarm"

    def test_ecs_task(self, ecs_task_event):
        assert classify(ecs_task_event) == "ecs_task"

    def test_unsupported_event_raises(self):
        with pytest.raises(ValueError, match="Unsupported event"):
            classify({"detail-type": "Unknown", "source": "aws.unknown"})


class TestLambdaHandler:
    @patch("handler.time.sleep")
    @patch("handler.sender")
    @patch("handler.renderer")
    @patch("handler.extract_alarm")
    def test_sqs_unwrap_dispatch_render_send(
        self, mock_extract, mock_renderer, mock_sender, mock_sleep,
        sqs_wrapped_event, mock_context
    ):
        """SQSアンラップ→抽出→レンダリング→送信の正常フロー"""
        mock_extract.return_value = {"priority": "ALARM", "monitor_id": "test"}
        mock_renderer.render.return_value = ("subject", "text", "<html>")

        from handler import lambda_handler
        lambda_handler(sqs_wrapped_event, mock_context)

        mock_extract.assert_called_once()
        mock_renderer.render.assert_called_once()
        mock_sender.send.assert_called_once_with("subject", "text", "<html>")

    @patch("handler.time.sleep")
    @patch("handler.sender")
    @patch("handler.extract_ecs")
    def test_skip_when_extract_returns_none(self, mock_extract, mock_sender, mock_sleep, mock_context):
        """extract()がNone返却時は送信スキップ"""
        mock_extract.return_value = None
        event = {
            "Records": [{"body": json.dumps({
                "source": "aws.ecs",
                "detail-type": "ECS Task State Change",
                "detail": {"containers": [{"exitCode": 0}]}
            })}]
        }
        from handler import lambda_handler
        lambda_handler(event, mock_context)
        mock_sender.send.assert_not_called()
