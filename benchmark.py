"""性能对比工具：实测「串行检测」与「并发检测」的耗时差。

用法：
    python benchmark.py              # 默认测 3 个站点
    python benchmark.py --repeat 3   # 每个站点测 3 轮取平均值

背景：v1/v2 里 HTTP 请求和 SSL 握手是串行执行的，总耗时 = 两者之和。
v3 改成并行后，总耗时应该接近两者中较慢的那个。
"""

import argparse
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import cache
from app.checker import _fetch_http, check_ssl, check_url, normalize_url

DEFAULT_TARGETS = [
    "https://www.baidu.com",
    "https://www.bing.com",
    "https://terraria.wiki.gg",
]


def serial_check(url: str, timeout: float = 5.0) -> None:
    """串行版本：先 HTTP，再 SSL —— 模拟 v1/v2 的做法。"""
    normalized = normalize_url(url)
    parsed = urlparse(normalized)

    _fetch_http(normalized, timeout)
    if parsed.scheme == "https" and parsed.hostname:
        check_ssl(parsed.hostname, 443, timeout)


def parallel_check(url: str) -> None:
    """并发版本：走 v3 的 check_url（关掉缓存，确保真的发请求）。"""
    check_url(url, use_cache=False)


def run(targets: list[str], repeat: int) -> None:
    header = f"{'网址':<34}{'串行(ms)':>12}{'并发(ms)':>12}{'提速':>9}"
    print("=" * len(header.encode("gbk", errors="replace")))
    print(header)
    print("-" * (len(header.encode("gbk", errors="replace"))))

    serial_all, parallel_all = [], []

    for target in targets:
        serial_times, parallel_times = [], []
        ok = True

        for _ in range(repeat):
            try:
                t0 = time.perf_counter()
                serial_check(target)
                serial_times.append((time.perf_counter() - t0) * 1000)
            except Exception as exc:
                print(f"{target:<34} 串行失败：{exc}")
                ok = False
                break

            try:
                t0 = time.perf_counter()
                parallel_check(target)
                parallel_times.append((time.perf_counter() - t0) * 1000)
            except Exception as exc:
                print(f"{target:<34} 并发失败：{exc}")
                ok = False
                break

        if not ok:
            continue

        serial_ms = statistics.mean(serial_times)
        parallel_ms = statistics.mean(parallel_times)
        speedup = serial_ms / parallel_ms if parallel_ms else 0

        serial_all.append(serial_ms)
        parallel_all.append(parallel_ms)
        print(f"{target:<34}{serial_ms:>12.0f}{parallel_ms:>12.0f}{speedup:>8.2f}x")

    if serial_all:
        total_serial = sum(serial_all)
        total_parallel = sum(parallel_all)
        total_speedup = total_serial / total_parallel if total_parallel else 0
        print("-" * (len(header.encode("gbk", errors="replace"))))
        print(
            f"{'合计':<34}{total_serial:>12.0f}{total_parallel:>12.0f}{total_speedup:>8.2f}x"
        )

    print("=" * (len(header.encode("gbk", errors="replace"))))
    print("说明：")
    print("  · 只统计网络探测耗时，不含 Python 启动开销；")
    print("  · 站点 SSL 握手越慢，并发收益越明显；")
    print("  · 不可达地址受 5 秒超时上限约束，两种模式耗时都会接近上限。")


def main():
    parser = argparse.ArgumentParser(description="串行 vs 并发 性能对比")
    parser.add_argument(
        "--repeat", type=int, default=1, help="每个站点重复次数，取平均值（默认 1）"
    )
    parser.add_argument(
        "targets", nargs="*", default=None, help="要测的网址，留空则用默认列表"
    )
    args = parser.parse_args()

    cache.clear()  # 确保不命中缓存，测的是真实网络耗时
    run(args.targets or DEFAULT_TARGETS, max(1, args.repeat))


if __name__ == "__main__":
    main()
