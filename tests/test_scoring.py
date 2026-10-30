"""scoring 模块测试：覆盖各维度打分档位与建议文案的对应关系。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scoring import score_report


def make_report(**overrides):
    """构造一份「满分」报告，测试时只改需要变化的字段。"""
    report = {
        "url": "https://example.com",
        "reached": True,
        "status_code": 200,
        "elapsed_ms": 100.0,
        "ssl": {"ok": True, "days_left": 90, "issuer": "Test CA"},
        "headers": {
            "strict-transport-security": {"present": True, "label": "HSTS 强制 HTTPS"},
            "content-security-policy": {"present": True, "label": "内容安全策略 CSP"},
            "x-content-type-options": {"present": True, "label": "禁止 MIME 类型嗅探"},
            "x-frame-options": {"present": True, "label": "防点击劫持"},
            "referrer-policy": {"present": True, "label": "Referrer 策略"},
        },
        "error": None,
    }
    report.update(overrides)
    return report


class TestScoreReport:
    def test_perfect_site_gets_100(self):
        result = score_report(make_report())
        assert result["score"] == 100
        assert result["grade"] == "优秀"
        assert result["suggestions"] == []

    def test_score_never_exceeds_bounds(self):
        result = score_report(make_report())
        assert 0 <= result["score"] <= 100

    def test_http_site_loses_https_points(self):
        report = make_report(url="http://example.com")
        report["ssl"] = {"ok": False, "error": "非 HTTPS 站点，未启用证书"}
        result = score_report(report)
        assert result["score"] <= 75
        assert any("HTTPS" in s for s in result["suggestions"])

    @pytest.mark.parametrize(
        "days_left, should_warn",
        [
            (90, False),   # 充足，不提示
            (20, True),    # <30 天，建议自动续期
            (5, True),     # <7 天，严重
            (-3, True),    # 已过期，严重
        ],
    )
    def test_cert_expiry_levels(self, days_left, should_warn):
        report = make_report()
        report["ssl"] = {"ok": True, "days_left": days_left, "issuer": "Test CA"}
        result = score_report(report)
        has_cert_warning = any("证书" in s for s in result["suggestions"])
        assert has_cert_warning is should_warn

    @pytest.mark.parametrize(
        "elapsed, min_speed_score",
        [(100, 15), (700, 12), (1200, 9), (2500, 6), (10000, 0)],
    )
    def test_speed_tiers(self, elapsed, min_speed_score):
        result = score_report(make_report(elapsed_ms=elapsed))
        speed = next(i for i in result["breakdown"] if i["name"] == "响应速度")
        assert speed["score"] == min_speed_score

    def test_missing_headers_deduct_and_suggest(self):
        report = make_report()
        for info in report["headers"].values():
            info["present"] = False
        result = score_report(report)
        header_item = next(i for i in result["breakdown"] if i["name"] == "安全响应头")
        assert header_item["score"] == 0
        assert len([s for s in result["suggestions"] if "安全响应头" in s]) == 5

    def test_unreachable_site_low_score(self):
        report = make_report(
            reached=False,
            status_code=None,
            elapsed_ms=5001.0,
            ssl={"ok": False, "error": "连接失败"},
            headers={},
            error="请求超时（超过 5.0 秒）",
        )
        result = score_report(report)
        assert result["score"] < 30
        assert result["grade"] == "需要改进"
        assert any("无法正常访问" in s for s in result["suggestions"])

    def test_critical_suggestions_come_first(self):
        report = make_report(url="http://example.com", elapsed_ms=6000.0)
        report["ssl"] = {"ok": False, "error": "非 HTTPS 站点，未启用证书"}
        result = score_report(report)
        assert "HTTPS" in result["suggestions"][0]

    def test_breakdown_has_all_dimensions(self):
        result = score_report(make_report())
        names = [item["name"] for item in result["breakdown"]]
        assert names == ["可达性", "状态码", "响应速度", "HTTPS 证书", "安全响应头"]
        assert all(item["score"] <= item["max"] for item in result["breakdown"])
