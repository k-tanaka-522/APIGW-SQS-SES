import pytest
import base64
import zlib
import json


class TestCloudWatchLogsExtract:
    def test_normal_extraction(self, cloudwatch_logs_event, mock_context):
        """正常抽出（2件のlogEvents）"""
        from handlers.cloudwatch_logs import extract
        result = extract(cloudwatch_logs_event, mock_context)

        assert result["priority"] == "ALARM"
        assert result["monitor_id"] == "/ecs/my-app"
        assert result["monitor_detail"] == "ecs/container/abc123"
        assert "Connection timeout" in result["message"]

    def test_multiple_log_events_in_org_message(self, cloudwatch_logs_event, mock_context):
        """org_messageに全件含まれること"""
        from handlers.cloudwatch_logs import extract
        result = extract(cloudwatch_logs_event, mock_context)

        assert "Connection timeout" in result["org_message"]
        assert "Retry failed" in result["org_message"]

    def test_truncation_over_5_events(self, mock_context):
        """6件以上のlogEventsで先頭5件+残件数になること"""
        raw = json.dumps({
            "logGroup": "/ecs/app",
            "logStream": "stream",
            "logEvents": [
                {"timestamp": 1707872400000, "message": f"ERROR line {i}"}
                for i in range(8)
            ]
        })
        compressed = zlib.compress(raw.encode(), wbits=16 + zlib.MAX_WBITS)
        event = {"awslogs": {"data": base64.b64encode(compressed).decode()}}

        from handlers.cloudwatch_logs import extract
        result = extract(event, mock_context)

        assert "他 3 件" in result["message"]
        assert "ERROR line 7" in result["org_message"]
