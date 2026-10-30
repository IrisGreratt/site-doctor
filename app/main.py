"""Site Doctor - 网站健康诊断工具

入口文件：创建 FastAPI 应用，挂载静态资源，提供首页、历史与监控路由。

V2 新增：
- 每次诊断自动落库，可在 /history 查看历史
- /monitors 管理监控项
- 启动时拉起后台定时检查任务（见 app/monitor.py）
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import storage
from app.checker import InvalidUrlError, check_url, normalize_url
from app.monitor import collect_cert_alerts, run_scheduler
from app.scoring import score_report

# 项目根目录（site-doctor/），用来定位 templates 和 static
BASE_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭时执行的逻辑。

    lifespan 是 FastAPI 推荐的启动钩子（比过时的 @app.on_event 更清晰）：
    yield 之前 = 启动时，yield 之后 = 关闭时。
    """
    storage.init_db()
    task = run_scheduler()
    yield
    task.cancel()


app = FastAPI(
    title="Site Doctor",
    description="网站健康诊断工具",
    version="0.2.0",
    lifespan=lifespan,
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _render(request: Request, template: str, context: dict):
    """统一渲染入口，避免每个路由重复写 TemplateResponse。

    证书到期提醒是「全局提示」，每个页面都可能要显示，
    所以在这里统一注入，而不是让每个路由自己查。
    """
    context.setdefault("title", "Site Doctor")
    context.setdefault("cert_alerts", collect_cert_alerts())
    return templates.TemplateResponse(request=request, name=template, context=context)


def _diagnose(url: str) -> dict:
    """跑一次完整诊断，返回模板需要的结构。失败时抛异常由调用方处理。"""
    report = check_url(url)
    scored = score_report(report)
    return {
        "report": report,
        "score": scored["score"],
        "grade": scored["grade"],
        "breakdown": scored["breakdown"],
        "suggestions": scored["suggestions"],
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, url: str | None = None):
    """首页：没有 url 参数时是空表单，有参数时直接诊断并展示结果。"""
    context = {
        "url_input": url or "",
        "result": None,
        "error": None,
        "total_checks": storage.total_checks(),
        "monitor_count": len(storage.list_monitors()),
    }

    if url and url.strip():
        try:
            result = _diagnose(url)
            context["result"] = result
            context["url_input"] = result["report"]["url"]
            # 落库：写库失败不应该影响用户看到诊断结果，所以单独 try
            try:
                storage.save_check(
                    url=result["report"]["url"],
                    score=result["score"],
                    grade=result["grade"],
                    report=result["report"],
                )
            except Exception:
                pass
        except InvalidUrlError as exc:
            context["error"] = str(exc)
        except Exception as exc:  # 兜底：任何意外都不能让页面白屏
            context["error"] = f"诊断过程出现意外错误：{exc}"

    return _render(request, "index.html", context)


@app.get("/history", response_class=HTMLResponse)
async def history(request: Request, url: str | None = None):
    """历史记录页：可按网址过滤。"""
    records = storage.list_history(url=url or None, limit=100)
    # 同一网址如果有多条，额外标出「比上一条涨了还是跌了」，方便看趋势
    by_url: dict[str, list[dict]] = {}
    for record in records:
        by_url.setdefault(record["url"], []).append(record)
    for items in by_url.values():
        # items 是时间倒序，逐个和更早一条比较
        for i, item in enumerate(items):
            item["delta"] = None
            if i + 1 < len(items):
                item["delta"] = item["score"] - items[i + 1]["score"]

    return _render(
        request,
        "history.html",
        {
            "title": "历史记录",
            "records": records,
            "filter_url": url or "",
            "total": storage.total_checks(),
        },
    )


@app.get("/history/{item_id}", response_class=HTMLResponse)
async def history_detail(request: Request, item_id: int):
    """查看某条历史记录的完整报告（复用结果页模板）。"""
    item = storage.get_history_item(item_id)
    if item is None:
        return _render(
            request,
            "index.html",
            {
                "url_input": "",
                "result": None,
                "error": "找不到这条历史记录",
                "total_checks": storage.total_checks(),
                "monitor_count": len(storage.list_monitors()),
            },
        )

    scored = score_report(item["report"])
    return _render(
        request,
        "index.html",
        {
            "url_input": item["url"],
            "result": {
                "report": item["report"],
                "score": scored["score"],
                "grade": scored["grade"],
                "breakdown": scored["breakdown"],
                "suggestions": scored["suggestions"],
            },
            "error": None,
            "history_id": item["id"],
            "history_time": item["created_at"],
            "total_checks": storage.total_checks(),
            "monitor_count": len(storage.list_monitors()),
        },
    )


@app.get("/monitors")
async def monitors_page(request: Request):
    """监控列表页。"""
    return _render(
        request,
        "monitors.html",
        {
            "title": "监控列表",
            "monitors": storage.list_monitors(),
            "total_checks": storage.total_checks(),
            "monitor_count": len(storage.list_monitors()),
            "min_interval": storage.MIN_INTERVAL_MIN,
        },
    )


@app.post("/monitors")
async def add_monitor_form(request: Request):
    """表单提交：添加监控项。"""
    form = await request.form()
    url = str(form.get("url") or "").strip()
    interval_raw = form.get("interval_min") or "60"
    try:
        interval = int(str(interval_raw))
    except ValueError:
        interval = 60

    if url:
        try:
            # 先用 normalize_url 校验一下，避免把垃圾数据写进库
            normalized = normalize_url(url)
            storage.add_monitor(normalized, interval)
        except InvalidUrlError:
            pass  # 非法网址不写入，静默返回列表页

    return RedirectResponse(url="/monitors", status_code=303)


@app.post("/monitors/{monitor_id}/delete")
async def delete_monitor(monitor_id: int):
    """删除监控项。"""
    storage.remove_monitor(monitor_id)
    return RedirectResponse(url="/monitors", status_code=303)
