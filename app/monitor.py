"""后台定时检查：周期性扫描监控列表，跑诊断、落库、更新状态，并收集到期提醒。

为什么用 asyncio 循环而不是 APScheduler？
- 项目只有一个很简单的需求（按间隔检查），引入调度框架属于过度设计；
- 用 asyncio.create_task + 轮询就能讲清楚原理，面试时也好解释「为什么这样选」；
- 没有额外依赖，部署更简单。

注意：诊断本身是同步阻塞的网络请求，所以放到线程池里跑（asyncio.to_thread），
否则会卡住整个事件循环，导致 Web 请求也没法响应 —— 这是很值得讲的一点。
"""

import asyncio
from datetime import datetime

from app import storage
from app.checker import InvalidUrlError, check_url
from app.scoring import score_report

# 轮询间隔：每 60 秒醒一次，看看有没有到期的监控项
POLL_SECONDS = 60

# 证书剩余多少天以内需要警告
CERT_WARN_DAYS = 30


def cert_alert_days(report: dict) -> int | None:
    """返回证书剩余天数；若非 HTTPS / 证书异常 / 剩余天数 >= 阈值则返回 None。

    单独抽成函数是为了方便测试：这是纯逻辑，不需要真的发网络请求。
    """
    ssl_info = report.get("ssl") or {}
    days = ssl_info.get("days_left")
    if not isinstance(days, int):
        return None
    if days < CERT_WARN_DAYS:
        return days
    return None


def run_monitor_once(item: dict) -> dict:
    """对一个监控项跑一次完整检查，并把结果写回数据库。"""
    url = item["url"]
    try:
        report = check_url(url)
        scored = score_report(report)
        storage.save_check(
            url=report["url"],
            score=scored["score"],
            grade=scored["grade"],
            report=report,
        )
        storage.update_monitor_result(item["id"], scored["score"], report.get("error"))
        return {
            "url": url,
            "score": scored["score"],
            "cert_days": cert_alert_days(report),
            "error": report.get("error"),
        }
    except InvalidUrlError as exc:
        storage.update_monitor_result(item["id"], None, str(exc))
        return {"url": url, "score": None, "cert_days": None, "error": str(exc)}
    except Exception as exc:  # 单个监控项失败不能影响其他项
        storage.update_monitor_result(item["id"], None, f"检查异常：{exc}")
        return {"url": url, "score": None, "cert_days": None, "error": f"检查异常：{exc}"}


async def _check_all_due() -> list[dict]:
    """检查所有到期项。同步的诊断放线程池，避免阻塞事件循环。"""
    due = storage.due_monitors()
    results = []
    for item in due:
        result = await asyncio.to_thread(run_monitor_once, item)
        results.append(result)
    return results


async def scheduler_loop() -> None:
    """常驻协程：启动后先睡一个轮询周期，再不断检查到期项。"""
    try:
        while True:
            try:
                await _check_all_due()
            except Exception:
                # 调度循环本身不能挂掉，否则整个监控就静默失效了
                pass
            await asyncio.sleep(POLL_SECONDS)
    except asyncio.CancelledError:
        # 应用关闭时正常退出
        raise


def run_scheduler() -> asyncio.Task:
    """创建后台任务，返回 task 对象供 lifespan 在关闭时 cancel。

    必须在事件循环里调用：lifespan 本身就是 async 函数，
    里面有运行中的循环，所以这里可以直接 create_task。
    """
    return asyncio.get_running_loop().create_task(scheduler_loop())


def collect_cert_alerts() -> list[dict]:
    """扫描监控列表，收集证书快过期的提醒。

    只对「最近检查过」的监控项检查，避免把很久以前的旧数据当成当前状态。
    """
    alerts = []
    for item in storage.list_monitors():
        if not item.get("last_checked"):
            continue
        full = storage.latest_check(item["url"])
        if not full:
            continue
        days = cert_alert_days(full["report"])
        if days is not None:
            alerts.append({"url": item["url"], "days": days})
    return alerts


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
