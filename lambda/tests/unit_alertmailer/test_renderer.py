import pytest


class TestRenderer:
    def test_standard_style_html(self, field_map, priority_map):
        """統一スタイルのHTML生成"""
        from renderer import render
        fields = {
            "priority": "ALARM",
            "env_name": "テスト環境",
            "plugin_name": "CloudWatch Alarm",
            "monitor_id": "test-alarm",
            **{f["key"]: "-" for f in field_map["fields"]}
        }
        subject, body_text, body_html = render(fields, field_map, priority_map)

        assert "#0f1c50" in body_html    # ヘッダ色
        assert "#6785c1" in body_html    # 行ヘッダ色
        assert 'class="Critical"' in body_html
        assert "危険" in subject

    def test_warning_css_class(self, field_map, priority_map):
        """TASK_STOPPEDでWarningクラス適用"""
        from renderer import render
        fields = {
            "priority": "TASK_STOPPED",
            "env_name": "テスト",
            "plugin_name": "ECS",
            "monitor_id": "td",
            **{f["key"]: "-" for f in field_map["fields"]}
        }
        _, _, body_html = render(fields, field_map, priority_map)
        assert 'class="Warning"' in body_html

    def test_html_escape_xss(self, field_map, priority_map):
        """特殊文字がエスケープされること"""
        from renderer import render
        fields = {
            **{f["key"]: "-" for f in field_map["fields"]},
            "priority": "ALARM",
            "env_name": "テスト",
            "plugin_name": "Test",
            "monitor_id": "test",
            "message": '<script>alert("xss")</script>',
        }
        _, _, body_html = render(fields, field_map, priority_map)
        assert "<script>" not in body_html
        assert "&lt;script&gt;" in body_html

    def test_plain_text_generation(self, field_map, priority_map):
        """プレーンテキスト版が全項目含むこと"""
        from renderer import render
        fields = {
            "priority": "OK",
            "env_name": "テスト",
            "plugin_name": "Test",
            "monitor_id": "test",
            **{f["key"]: "-" for f in field_map["fields"]}
        }
        _, body_text, _ = render(fields, field_map, priority_map)
        assert "環境:" in body_text
        assert "重要度:" in body_text
