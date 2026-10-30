"""storage 模块测试：用独立数据库文件，覆盖增删改查与到期判断。

db 夹具定义在 tests/conftest.py 里，每个测试一个独立库。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage


FAKE_REPORT = {"url": "https://example.com", "reached": True, "status_code": 200}


class TestChecks:
    def test_save_and_count(self, db):
        assert storage.total_checks(db) == 0
        storage.save_check("https://a.com", 90, "优秀", FAKE_REPORT, db)
        storage.save_check("https://b.com", 60, "及格", FAKE_REPORT, db)
        assert storage.total_checks(db) == 2

    def test_list_history_filters_by_url(self, db):
        storage.save_check("https://a.com", 90, "优秀", FAKE_REPORT, db)
        storage.save_check("https://a.com", 70, "及格", FAKE_REPORT, db)
        storage.save_check("https://b.com", 50, "需要改进", FAKE_REPORT, db)

        only_a = storage.list_history("https://a.com", db_path=db)
        assert len(only_a) == 2
        assert all(r["url"] == "https://a.com" for r in only_a)
        # 时间倒序：后插入的在前
        assert only_a[0]["score"] == 70

        assert len(storage.list_history(db_path=db)) == 3

    def test_list_history_respects_limit(self, db):
        for i in range(5):
            storage.save_check(f"https://x{i}.com", i, "及格", FAKE_REPORT, db)
        assert len(storage.list_history(limit=3, db_path=db)) == 3

    def test_get_history_item_parses_report(self, db):
        item_id = storage.save_check("https://a.com", 88, "良好", FAKE_REPORT, db)
        item = storage.get_history_item(item_id, db)
        assert item["report"]["status_code"] == 200
        assert item["score"] == 88

    def test_get_missing_item_returns_none(self, db):
        assert storage.get_history_item(9999, db) is None

    def test_latest_check_returns_newest(self, db):
        storage.save_check("https://a.com", 50, "需要改进", FAKE_REPORT, db)
        storage.save_check("https://a.com", 95, "优秀", FAKE_REPORT, db)
        latest = storage.latest_check("https://a.com", db)
        assert latest["score"] == 95

    def test_latest_check_missing_url(self, db):
        assert storage.latest_check("https://none.com", db) is None


class TestMonitors:
    def test_add_and_list(self, db):
        storage.add_monitor("https://a.com", 60, db)
        monitors = storage.list_monitors(db)
        assert len(monitors) == 1
        assert monitors[0]["interval_min"] == 60
        assert monitors[0]["last_checked"] is None

    def test_duplicate_url_updates_interval(self, db):
        first = storage.add_monitor("https://a.com", 60, db)
        second = storage.add_monitor("https://a.com", 15, db)
        assert first == second
        assert len(storage.list_monitors(db)) == 1
        assert storage.list_monitors(db)[0]["interval_min"] == 15

    def test_interval_lower_bound(self, db):
        storage.add_monitor("https://a.com", 1, db)
        assert storage.list_monitors(db)[0]["interval_min"] == storage.MIN_INTERVAL_MIN

    def test_remove_monitor(self, db):
        mid = storage.add_monitor("https://a.com", 60, db)
        assert storage.remove_monitor(mid, db) is True
        assert storage.list_monitors(db) == []
        assert storage.remove_monitor(mid, db) is False

    def test_update_monitor_result(self, db):
        mid = storage.add_monitor("https://a.com", 60, db)
        storage.update_monitor_result(mid, 88, None, db)
        item = storage.list_monitors(db)[0]
        assert item["last_score"] == 88
        assert item["last_checked"] is not None
        assert item["last_error"] is None

    def test_update_monitor_result_with_error(self, db):
        mid = storage.add_monitor("https://a.com", 60, db)
        storage.update_monitor_result(mid, None, "请求超时", db)
        item = storage.list_monitors(db)[0]
        assert item["last_score"] is None
        assert item["last_error"] == "请求超时"


class TestDueMonitors:
    def test_never_checked_is_due(self, db):
        storage.add_monitor("https://a.com", 60, db)
        assert len(storage.due_monitors(db_path=db)) == 1

    def test_recently_checked_is_not_due(self, db):
        mid = storage.add_monitor("https://a.com", 60, db)
        storage.update_monitor_result(mid, 90, None, db)
        assert storage.due_monitors(db_path=db) == []

    def test_due_after_interval_passes(self, db):
        mid = storage.add_monitor("https://a.com", 60, db)
        storage.update_monitor_result(mid, 90, None, db)
        future = datetime.now() + timedelta(minutes=61)
        assert len(storage.due_monitors(now=future, db_path=db)) == 1

    def test_grace_seconds_tolerates_slight_early_tick(self, db):
        mid = storage.add_monitor("https://a.com", 60, db)
        storage.update_monitor_result(mid, 90, None, db)
        # 只过了 60 分钟差 20 秒，容差 30 秒内应当算到期
        almost = datetime.now() + timedelta(minutes=60) - timedelta(seconds=20)
        assert len(storage.due_monitors(now=almost, db_path=db)) == 1

    def test_mixed_monitors(self, db):
        due_id = storage.add_monitor("https://due.com", 60, db)
        fresh_id = storage.add_monitor("https://fresh.com", 60, db)
        storage.update_monitor_result(fresh_id, 90, None, db)
        due = storage.due_monitors(db_path=db)
        assert [m["id"] for m in due] == [due_id]
