import pytest
from unittest.mock import patch, MagicMock


class TestCloudWatchAlarmExtract:
    @patch("handlers.cloudwatch_alarm.cw")
    def test_metric_alarm_normal(self, mock_cw, cloudwatch_alarm_event, mock_context):
        """単一Metricアラーム正常抽出"""
        mock_cw.describe_alarms.return_value = {
            "MetricAlarms": [{
                "AlarmName": "test-cpu-alarm",
                "Namespace": "AWS/EC2",
                "MetricName": "CPUUtilization",
                "AlarmDescription": "CPU使用率アラーム",
                "Dimensions": [{"Name": "InstanceId", "Value": "i-12345"}],
            }],
            "CompositeAlarms": []
        }
        from handlers.cloudwatch_alarm import extract
        result = extract(cloudwatch_alarm_event, mock_context)

        assert result["priority"] == "ALARM"
        assert result["monitor_id"] == "test-cpu-alarm"
        assert result["monitor_detail"] == "AWS/EC2/CPUUtilization"
        assert result["scope"] == "InstanceId=i-12345"
        assert result["notify_uuid"] == "test-request-id-12345"

    @patch("handlers.cloudwatch_alarm.cw")
    def test_composite_alarm_same_handler(self, mock_cw, mock_context):
        """複合アラームも同じextractで処理可能"""
        event = {
            "source": "aws.cloudwatch",
            "detail-type": "CloudWatch Alarm State Change",
            "time": "2026-02-14T01:00:00Z",
            "region": "ap-northeast-1",
            "detail": {
                "alarmName": "composite-ecs-alarm",
                "state": {"value": "ALARM", "reason": "child alarm triggered"}
            }
        }
        mock_cw.describe_alarms.return_value = {
            "MetricAlarms": [],
            "CompositeAlarms": [{
                "AlarmName": "composite-ecs-alarm",
                "AlarmDescription": "ECS片系停止検知",
                "AlarmRule": "ALARM(task-a) OR ALARM(task-b)",
            }]
        }
        from handlers.cloudwatch_alarm import extract
        result = extract(event, mock_context)

        assert result["monitor_id"] == "composite-ecs-alarm"
        assert result["plugin_name"] == "CloudWatch Alarm"

    @patch("handlers.cloudwatch_alarm.cw")
    def test_describe_alarms_failure_fallback(self, mock_cw, cloudwatch_alarm_event, mock_context):
        """DescribeAlarms失敗時にevent情報だけでfallback"""
        mock_cw.describe_alarms.side_effect = Exception("AccessDenied")

        from handlers.cloudwatch_alarm import extract
        result = extract(cloudwatch_alarm_event, mock_context)

        assert result["monitor_id"] == "test-cpu-alarm"
        assert "Threshold Crossed" in result["message"]
        # DescribeAlarms失敗でもNoneにならない
        assert result is not None
