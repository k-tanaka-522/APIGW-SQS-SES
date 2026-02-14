import pytest
from unittest.mock import patch, MagicMock


class TestLambdaBootstrap:
    @patch("lambda_common.decorator.init_tracer")
    @patch("lambda_common.decorator.get_logger")
    def test_start_end_log_on_success(self, mock_get_logger, mock_tracer, mock_context):
        """正常終了時にSTART→ENDログが出ること"""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        from lambda_common.decorator import lambda_bootstrap

        @lambda_bootstrap(service_name="test-svc")
        def handler(event, context, logger=None):
            return {"statusCode": 200}

        handler({"Records": []}, mock_context)

        assert mock_logger.info.call_count == 2
        start_output = mock_logger.info.call_args_list[0][0][0]
        end_output = mock_logger.info.call_args_list[1][0][0]
        assert start_output.startswith("START ")
        assert "test-svc" in start_output
        assert "test-request-id-12345" in start_output
        assert end_output.startswith("END ")
        assert "SUCCESS" in end_output

    @patch("lambda_common.decorator.init_tracer")
    @patch("lambda_common.decorator.get_logger")
    def test_error_log_and_reraise(self, mock_get_logger, mock_tracer, mock_context):
        """異常時にERRORログ出力→例外再raise"""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        from lambda_common.decorator import lambda_bootstrap

        @lambda_bootstrap(service_name="test-svc")
        def bad_handler(event, context, logger=None):
            raise RuntimeError("test error")

        with pytest.raises(RuntimeError, match="test error"):
            bad_handler({}, mock_context)

        mock_logger.error.assert_called_once()
        err_output = mock_logger.error.call_args[0][0]
        first_line = err_output.split("\n")[0]
        assert first_line.startswith("ERROR ")
        assert "FAILURE" in first_line
        assert "RuntimeError" in first_line
        assert "test error" in first_line
        assert "Traceback" in err_output

    @patch("lambda_common.decorator.init_tracer")
    @patch("lambda_common.decorator.get_logger")
    def test_logger_passed_to_handler(self, mock_get_logger, mock_tracer, mock_context):
        """handlerにloggerが渡されること"""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        from lambda_common.decorator import lambda_bootstrap

        received_logger = None

        @lambda_bootstrap(service_name="test-svc")
        def handler(event, context, logger=None):
            nonlocal received_logger
            received_logger = logger

        handler({}, mock_context)
        assert received_logger is mock_logger
