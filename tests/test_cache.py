"""cache 模块测试：TTL、命中统计、线程安全的基本行为。

注意：涉及时间的测试用 monkeypatch 替换 time.time，
而不是真的 sleep —— 让测试跑得快且稳定。
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import cache


@pytest.fixture(autouse=True)
def clean_cache():
    """每个测试前后都清空缓存，避免相互污染。"""
    cache.clear()
    yield
    cache.clear()


REPORT = {"url": "https://a.com", "reached": True, "status_code": 200}


class TestCacheBasics:
    def test_miss_returns_none(self):
        assert cache.get("https://none.com") is None

    def test_put_then_get(self):
        cache.put("https://a.com", REPORT)
        got = cache.get("https://a.com")
        assert got is not None
        assert got["status_code"] == 200

    def test_different_urls_are_isolated(self):
        cache.put("https://a.com", {"score": 1})
        cache.put("https://b.com", {"score": 2})
        assert cache.get("https://a.com")["score"] == 1
        assert cache.get("https://b.com")["score"] == 2

    def test_returned_value_is_a_copy(self):
        """调用方修改返回值不能污染缓存里的原始数据。"""
        cache.put("https://a.com", {"score": 10})
        got = cache.get("https://a.com")
        got["score"] = 999
        assert cache.get("https://a.com")["score"] == 10

    def test_clear_empties_cache(self):
        cache.put("https://a.com", REPORT)
        cache.clear()
        assert cache.get("https://a.com") is None


class TestExpiry:
    def test_expired_entry_misses(self, monkeypatch):
        now = 1000.0
        monkeypatch.setattr(time, "time", lambda: now)

        cache.put("https://a.com", REPORT, ttl=60)
        assert cache.get("https://a.com") is not None

        # 跳到 61 秒后
        monkeypatch.setattr(time, "time", lambda: now + 61)
        assert cache.get("https://a.com") is None

    def test_exactly_at_ttl_expires(self, monkeypatch):
        now = 1000.0
        monkeypatch.setattr(time, "time", lambda: now)
        cache.put("https://a.com", REPORT, ttl=60)

        monkeypatch.setattr(time, "time", lambda: now + 60)
        # 边界：到点即过期
        assert cache.get("https://a.com") is None

    def test_still_valid_just_before_ttl(self, monkeypatch):
        now = 1000.0
        monkeypatch.setattr(time, "time", lambda: now)
        cache.put("https://a.com", REPORT, ttl=60)

        monkeypatch.setattr(time, "time", lambda: now + 59)
        assert cache.get("https://a.com") is not None

    def test_expired_entry_is_removed_from_dict(self, monkeypatch):
        """过期项应被顺手删除，避免字典无限膨胀。"""
        now = 1000.0
        monkeypatch.setattr(time, "time", lambda: now)
        cache.put("https://a.com", REPORT, ttl=60)

        monkeypatch.setattr(time, "time", lambda: now + 120)
        cache.get("https://a.com")
        assert cache.stats()["size"] == 0


class TestStats:
    def test_counts_hits_and_misses(self):
        cache.get("https://a.com")  # miss
        cache.put("https://a.com", REPORT)
        cache.get("https://a.com")  # hit

        stats = cache.stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 1
        assert stats["size"] == 1

    def test_default_ttl_is_60(self):
        assert cache.DEFAULT_TTL == 60


class TestIsCacheable:
    def test_successful_report_is_cacheable(self):
        assert cache.is_cacheable(REPORT) is True

    def test_unreached_report_not_cacheable(self):
        assert cache.is_cacheable({"reached": False, "error": "超时"}) is False

    def test_report_with_error_not_cacheable(self):
        assert cache.is_cacheable({"reached": True, "error": "部分失败"}) is False
