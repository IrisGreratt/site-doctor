"""checker 模块测试：重点覆盖 URL 整理、安全头检测和异常分支。

网络相关的 check_ssl / check_url 用 mock 隔离，保证测试离线可跑、结果稳定。
"""

import sys
from pathlib import Path

import pytest

# 让测试能 import app 包（从项目根目录运行时本不需要，但兼容从任意目录执行）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import checker
from app.checker import (
    InvalidUrlError,
    check_headers,
    normalize_url,
)


# ---------------- normalize_url ----------------

class TestNormalizeUrl:
    def test_keeps_https_url(self):
        assert normalize_url("https://example.com") == "https://example.com"

    def test_keeps_http_url(self):
        assert normalize_url("http://example.com") == "http://example.com"

    def test_adds_https_when_missing_scheme(self):
        assert normalize_url("example.com") == "https://example.com"

    def test_strips_whitespace(self):
        assert normalize_url("  https://example.com  ") == "https://example.com"

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_raises(self, bad):
        with pytest.raises(InvalidUrlError):
            normalize_url(bad)

    def test_unsupported_scheme_raises(self):
        with pytest.raises(InvalidUrlError, match="只支持 http / https"):
            normalize_url("ftp://example.com")

    def test_missing_hostname_raises(self):
        with pytest.raises(InvalidUrlError):
            normalize_url("http://")


# ---------------- check_headers ----------------

class TestCheckHeaders:
    def test_detects_all_missing(self):
        result = check_headers({})
        assert len(result) == len(checker.SECURITY_HEADERS)
        assert all(not info["present"] for info in result.values())

    def test_detects_present_header(self):
        result = check_headers({"strict-transport-security": "max-age=31536000"})
        assert result["strict-transport-security"]["present"] is True
        assert result["strict-transport-security"]["value"] == "max-age=31536000"
        assert result["content-security-policy"]["present"] is False

    def test_header_lookup_is_case_insensitive(self):
        # httpx.Headers 本身大小写不敏感，用真实类型验证一次
        import httpx

        headers = httpx.Headers({"X-Frame-Options": "DENY"})
        result = check_headers(headers)
        assert result["x-frame-options"]["present"] is True


# ---------------- check_ssl ----------------

class TestCheckSsl:
    def test_valid_cert_returns_days_left(self):
        """正常验证握手能拿到证书时，应返回剩余天数与颁发者。"""
        # 真实结构是嵌套元组：((('organizationName', 'Test CA'),),)
        # 第一层 = 若干 RDN，第二层 = 每个 RDN 里的键值对
        fake_cert = {
            "notAfter": "Dec 31 23:59:59 2099 GMT",
            "issuer": ((("organizationName", "Test CA"),),),
        }
        monkeypatch = pytest.MonkeyPatch()
        try:
            class FakeSock:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            class FakeTls:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def getpeercert(self, binary_form=False):
                    return fake_cert

            class FakeContext:
                def __init__(self, *a, **k):
                    pass

                def wrap_socket(self, sock, server_hostname=None):
                    return FakeTls()

            monkeypatch.setattr(checker.socket, "create_connection", lambda *a, **k: FakeSock())
            monkeypatch.setattr(checker.ssl, "create_default_context", lambda: FakeContext())

            result = checker.check_ssl("example.com")
        finally:
            monkeypatch.undo()

        assert result["ok"] is True
        assert result["days_left"] > 0
        assert result["issuer"] == "Test CA"

    def test_connection_error_returns_not_ok(self):
        import socket

        monkeypatch = pytest.MonkeyPatch()
        try:
            def boom(*args, **kwargs):
                raise socket.gaierror("no such host")

            monkeypatch.setattr(checker.socket, "create_connection", boom)
            result = checker.check_ssl("nope.invalid")
        finally:
            monkeypatch.undo()

        assert result["ok"] is False
        assert "连接失败" in result["error"]


# ---------------- check_url ----------------

class TestCheckUrl:
    def test_timeout_is_reported_not_raised(self):
        """超时应当被捕获成错误字段，而不是把异常抛给上层。"""
        import httpx

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url):
                raise httpx.TimeoutException("timeout")

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(checker.httpx, "Client", FakeClient)
            report = checker.check_url("https://slow.example")
        finally:
            monkeypatch.undo()

        assert report["reached"] is False
        assert "超时" in report["error"]
        assert report["status_code"] is None

    def test_invalid_url_raises(self):
        with pytest.raises(InvalidUrlError):
            checker.check_url("ftp://example.com")

    def test_http_site_marks_ssl_as_not_enabled(self):
        import httpx

        class FakeResponse:
            status_code = 200
            headers = httpx.Headers({"content-type": "text/html"})
            url = "http://example.com"

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url):
                return FakeResponse()

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(checker.httpx, "Client", FakeClient)
            report = checker.check_url("http://example.com")
        finally:
            monkeypatch.undo()

        assert report["reached"] is True
        assert report["status_code"] == 200
        assert "未启用证书" in report["ssl"]["error"]
