import json
import pytest
from unittest.mock import MagicMock


class TestLogStart:
    def test_sqs_event(self):
        from lambda_common.logger import log_start
        mock = MagicMock()
        log_start(mock, "svc", "req-001", {"Records": [1, 2, 3]})
        parsed = json.loads(mock.info.call_args[0][0])
        assert parsed["phase"] == "START"
        assert parsed["service"] == "svc"
        assert parsed["request_id"] == "req-001"
        assert "SQS records=3" in parsed["event_summary"]

    def test_cloudwatch_logs_event(self):
        from lambda_common.logger import log_start
        mock = MagicMock()
        log_start(mock, "svc", "req-001", {"awslogs": {}})
        parsed = json.loads(mock.info.call_args[0][0])
        assert parsed["event_summary"] == "CloudWatch Logs subscription"

    def test_detail_type_event(self):
        from lambda_common.logger import log_start
        mock = MagicMock()
        log_start(mock, "svc", "req-001", {"detail-type": "ECS Task State Change"})
        parsed = json.loads(mock.info.call_args[0][0])
        assert parsed["event_summary"] == "ECS Task State Change"


class TestLogEnd:
    def test_success_fields(self):
        from lambda_common.logger import log_end
        mock = MagicMock()
        log_end(mock, "svc", "req-001")
        parsed = json.loads(mock.info.call_args[0][0])
        assert parsed["phase"] == "END"
        assert parsed["status"] == "SUCCESS"
        assert parsed["service"] == "svc"


class TestLogError:
    def test_error_with_stacktrace(self):
        from lambda_common.logger import log_error
        mock = MagicMock()
        try:
            raise ValueError("broken")
        except ValueError as e:
            log_error(mock, "svc", "req-001", e)
        parsed = json.loads(mock.error.call_args[0][0])
        assert parsed["phase"] == "ERROR"
        assert parsed["status"] == "FAILURE"
        assert parsed["error_type"] == "ValueError"
        assert "broken" in parsed["error_message"]
        assert "Traceback" in parsed["stacktrace"]


class TestLogWarn:
    def test_warn_message(self):
        from lambda_common.logger import log_warn
        mock = MagicMock()
        log_warn(mock, "svc", "req-001", "skipping bad record")
        parsed = json.loads(mock.warning.call_args[0][0])
        assert parsed["phase"] == "WARN"
        assert parsed["message"] == "skipping bad record"
