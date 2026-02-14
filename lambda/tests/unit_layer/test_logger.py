import pytest
from unittest.mock import MagicMock


class TestLogStart:
    def test_sqs_event(self):
        from lambda_common.logger import log_start
        mock = MagicMock()
        log_start(mock, "svc", "req-001", {"Records": [1, 2, 3]})
        output = mock.info.call_args[0][0]
        assert output.startswith("START ")
        assert "svc" in output
        assert "req-001" in output
        assert "SQS records=3" in output

    def test_cloudwatch_logs_event(self):
        from lambda_common.logger import log_start
        mock = MagicMock()
        log_start(mock, "svc", "req-001", {"awslogs": {}})
        output = mock.info.call_args[0][0]
        assert "CloudWatch Logs subscription" in output

    def test_detail_type_event(self):
        from lambda_common.logger import log_start
        mock = MagicMock()
        log_start(mock, "svc", "req-001", {"detail-type": "ECS Task State Change"})
        output = mock.info.call_args[0][0]
        assert "ECS Task State Change" in output


class TestLogEnd:
    def test_success_fields(self):
        from lambda_common.logger import log_end
        mock = MagicMock()
        log_end(mock, "svc", "req-001")
        output = mock.info.call_args[0][0]
        assert output.startswith("END ")
        assert "svc" in output
        assert "SUCCESS" in output


class TestLogError:
    def test_error_with_stacktrace(self):
        from lambda_common.logger import log_error
        mock = MagicMock()
        try:
            raise ValueError("broken")
        except ValueError as e:
            log_error(mock, "svc", "req-001", e)
        output = mock.error.call_args[0][0]
        first_line = output.split("\n")[0]
        assert first_line.startswith("ERROR ")
        assert "svc" in first_line
        assert "FAILURE" in first_line
        assert "ValueError" in first_line
        assert "broken" in first_line
        assert "Traceback" in output


class TestLogWarn:
    def test_warn_message(self):
        from lambda_common.logger import log_warn
        mock = MagicMock()
        log_warn(mock, "svc", "req-001", "skipping bad record")
        output = mock.warning.call_args[0][0]
        assert output.startswith("WARN ")
        assert "skipping bad record" in output
