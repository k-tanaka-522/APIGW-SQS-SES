import pytest
from unittest.mock import patch


class TestSender:
    @patch("sender.ses")
    def test_send_normal(self, mock_ses):
        """正しいパラメータでSES送信"""
        from sender import send
        send("テスト件名", "テスト本文", "<html>テスト</html>")

        mock_ses.send_email.assert_called_once()
        args = mock_ses.send_email.call_args[1]
        assert "[TEST] テスト件名" in args["Message"]["Subject"]["Data"]
        assert args["Destination"]["ToAddresses"] == ["test-to@example.com"]

    def test_split_empty(self):
        from sender import _split
        assert _split("") == []

    def test_split_comma(self):
        from sender import _split
        assert _split("a@t.com, b@t.com") == ["a@t.com", "b@t.com"]

    def test_split_semicolon(self):
        from sender import _split
        assert _split("a@t.com;b@t.com") == ["a@t.com", "b@t.com"]
