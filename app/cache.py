"""TTL 缓存：同一网址在有效期内直接复用上次结果，避免重复发起网络请求。

为什么需要？
- 用户点「刷新」或反复诊断同一个网址时，没必要每次都真的去探测；
- 监控任务和手动诊断可能撞在一起，缓存能削掉重复开销。

实现要点：
1. 用「网址 + 当前时间戳」判断是否过期，不需要后台清理线程；
2. 线程安全：后台监控在线程池里跑，Web 请求在主线程，
   两者会同时读写缓存，所以加锁；
3. 只缓存「成功拿到结果」的报告；失败的结果不缓存，
   否则一个临时网络抖动会被缓存住 60 秒，用户重试也还是失败。
"""

import threading
import time

# 默认有效期（秒）
DEFAULT_TTL = 60

# 内存字典：key = 规范化后的网址，value = (过期时间戳, 报告)
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()

# 简单的命中统计，方便在页面/日志里看效果
_stats = {"hits": 0, "misses": 0}


def get(url: str) -> dict | None:
    """取缓存。命中且未过期返回报告副本，否则返回 None。"""
    with _lock:
        entry = _cache.get(url)
        if entry is None:
            _stats["misses"] += 1
            return None
        expires_at, report = entry
        if time.time() >= expires_at:
            # 过期了顺手删掉，避免字典无限增长
            _cache.pop(url, None)
            _stats["misses"] += 1
            return None

        _stats["hits"] += 1
        # 返回浅拷贝，避免调用方修改缓存里的原始数据
        return dict(report)


def put(url: str, report: dict, ttl: int = DEFAULT_TTL) -> None:
    """写入缓存。只应缓存成功的报告（调用方负责判断）。"""
    with _lock:
        _cache[url] = (time.time() + ttl, dict(report))


def clear() -> None:
    """清空缓存（测试和「强制刷新」用）。"""
    with _lock:
        _cache.clear()
        _stats["hits"] = 0
        _stats["misses"] = 0


def stats() -> dict:
    """当前缓存状态：条数与命中/未命中次数。"""
    with _lock:
        return {
            "size": len(_cache),
            "hits": _stats["hits"],
            "misses": _stats["misses"],
        }


def is_cacheable(report: dict) -> bool:
    """判断一份报告值不值得缓存。

    只缓存「站点确实响应了」的结果：
    - 超时 / 连接失败属于临时故障，缓存住会让用户重试也拿不到新结果；
    - 证书快过期属于会变化的状态，缓存太久没意义。
    """
    if not report.get("reached"):
        return False
    if report.get("error"):
        return False
    return True
