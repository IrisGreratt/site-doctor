"""report 模块测试：文件名安全性与 HTML 转义（防注入）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.report import build_report_html, report_filename


def make_result(**overrides):
    result = {
        "score": 78,
        "grade": "良好",
        "report": {
            "url": "https://example.com",
            "reached": True,
            "status_code": 200,
            "elapsed_ms": 123.4,
            "ssl": {"ok": True, "days_left": 50},
        },
        "breakdown": [
            {"name": "可达性", "score": 25, "max": 25, "detail": "站点可正常访问"},
            {"name": "安全响应头", "score": 5, "max": 25, "detail": "已配置 1/5"},
        ],
        "suggestions": ["缺少 HSTS", "响应偏慢"],
    }
    result.update(overrides)
    return result


class TestFilename:
    def test_strips_scheme(self):
        name = report_filename("https://www.baidu.com")
        assert name.startswith("www.baidu.com_")
        assert name.endswith(".html")

    def test_removes_path_slashes(self):
        name = report_filename("https://example.com/a/b/c")
        assert "/" not in name and "\\" not in name

    def test_removes_windows_illegal_chars(self):
        name = report_filename("https://example.com/?a=1&b=2")
        for ch in '<>:"\\|?*':
            assert ch not in name

    def test_empty_url_falls_back(self):
        assert report_filename("https://") .startswith("site_")


class TestBuildHtml:
    def test_contains_key_sections(self):
        html = build_report_html(make_result(), "https://example.com")
        assert "网站体检报告" in html
        assert "各项检查" in html
        assert "改进建议" in html
        assert "78" in html
        assert "良好" in html

    def test_is_self_contained(self):
        """不能有任何外部资源引用，否则离线打开会掉样式。"""
        html = build_report_html(make_result(), "https://example.com")
        assert 'src="http' not in html
        assert 'href="http' not in html
        assert "<style>" in html

    def test_escapes_html_in_url(self):
        """网址里若含 HTML 标签，必须被转义，不能注入脚本。"""
        evil = 'https://example.com/"><script>alert(1)</script>'
        html = build_report_html(make_result(), evil)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_escapes_suggestion_text(self):
        result = make_result(suggestions=['<img src=x onerror="alert(1)">'])
        html = build_report_html(result, "https://example.com")
        assert "<img src=x" not in html
        assert "&lt;img" in html

    def test_no_suggestions_shows_all_good(self):
        result = make_result(suggestions=[])
        html = build_report_html(result, "https://example.com")
        assert "没有发现需要改进的问题" in html

    def test_score_color_changes_by_level(self):
        great = build_report_html(make_result(score=95), "https://example.com")
        bad = build_report_html(make_result(score=30), "https://example.com")
        assert "#10b981" in great  # 绿色
        assert "#ef4444" in bad    # 红色
