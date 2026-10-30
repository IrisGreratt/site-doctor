"""monitor 模块测试：重点是证书到期判断这个纯逻辑函数。

注意：不测后台循环本身（它依赖事件循环和时间，属于集成测试范畴），
只测可以确定性验证的纯函数，这样测试稳定、跑得快。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import monitor
from app import storage as raw_storage


class TestCertAlertDays:
    def test_plenty_of_days_returns_none(self):
        report = {"ssl": {"ok": True, "days_left": 90}}
        assert monitor.cert_alert_days(report) is None

    def test_exactly_threshold_returns_none(self):
        """正好 30 天不告警（阈值是「不足 30 天」）。"""
        report = {"ssl": {"ok": True, "days_left": 30}}
        assert monitor.cert_alert_days(report) is None

    def test_below_threshold_warns(self):
        report = {"ssl": {"ok": True, "days_left": 29}}
        assert monitor.cert_alert_days(report) == 29

    def test_few_days_warns(self):
        report = {"ssl": {"ok": True, "days_left": 5}}
        assert monitor.cert_alert_days(report) == 5

    def test_expired_cert_warns_negative(self):
        """证书已过期时 days_left 是负数，也应该告警。"""
        report = {"ssl": {"ok": True, "days_left": -3}}
        assert monitor.cert_alert_days(report) == -3

    def test_non_https_returns_none(self):
        report = {"ssl": {"ok": False, "error": "非 HTTPS 站点，未启用证书"}}
        assert monitor.cert_alert_days(report) is None

    def test_missing_ssl_key_returns_none(self):
        assert monitor.cert_alert_days({}) is None

    def test_none_days_left_returns_none(self):
        report = {"ssl": {"ok": True, "days_left": None}}
        assert monitor.cert_alert_days(report) is None


class TestRunMonitorOnce:
    """验证 run_monitor_once 的落库与异常处理。

    注意：storage 各函数的 db_path=DB_PATH 默认值在函数定义时就已绑定，
    monkeypatch 替换 storage.DB_PATH 并不会改变它。因此这里用
    「把默认库临时指向测试库」的包装器，且 wrapper 只接受关键字参数传入，
    避免与位置参数冲突。
    """

    def _bind_db(self, monkeypatch, db):
        """让 monitor 里调用的 storage 函数都落到测试库。"""
        for name in ("save_check", "update_monitor_result"):
            original_fn = getattr(raw_storage, name)

            def wrapper(*args, _fn=original_fn, **kwargs):
                kwargs["db_path"] = db
                return _fn(*args, **kwargs)

            monkeypatch.setattr(raw_storage, name, wrapper)

    def test_writes_result_to_storage(self, db, monkeypatch):
        """检查成功后应落库，并回写监控项状态。"""
        mid = raw_storage.add_monitor("https://example.com", 60, db)
        self._bind_db(monkeypatch, db)

        monkeypatch.setattr(
            monitor,
            "check_url",
            lambda url: {
                "url": "https://example.com",
                "reached": True,
                "status_code": 200,
                "elapsed_ms": 100.0,
                "ssl": {"ok": True, "days_left": 200, "issuer": "CA"},
                "headers": {},
                "error": None,
            },
        )

        result = monitor.run_monitor_once({"id": mid, "url": "https://example.com"})

        assert result["score"] is not None
        assert result["cert_days"] is None
        assert raw_storage.total_checks(db) == 1
        item = raw_storage.list_monitors(db)[0]
        assert item["last_score"] == result["score"]

    def test_exception_is_captured_not_raised(self, db, monkeypatch):
        """单个监控项出错不能抛出，否则会中断整批检查。"""
        mid = raw_storage.add_monitor("https://bad.com", 60, db)
        self._bind_db(monkeypatch, db)

        def boom(url):
            raise RuntimeError("网络炸了")

        monkeypatch.setattr(monitor, "check_url", boom)

        result = monitor.run_monitor_once({"id": mid, "url": "https://bad.com"})

        assert result["score"] is None
        assert "检查异常" in result["error"]
        item = raw_storage.list_monitors(db)[0]
        assert "检查异常" in item["last_error"]
