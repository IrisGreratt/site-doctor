"""Site Doctor - 网站健康诊断工具

入口文件：创建 FastAPI 应用，挂载静态资源，提供首页与诊断路由。
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.checker import InvalidUrlError, check_url
from app.scoring import score_report

# 项目根目录（site-doctor/），用来定位 templates 和 static
BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="Site Doctor", description="网站健康诊断工具", version="0.1.0")

# 模板目录，Jinja2 负责把 HTML + 变量拼成最终页面
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 静态文件（CSS/JS），浏览器访问 /static/xxx 时会映射到这个目录
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _render(request: Request, context: dict):
    """统一渲染入口，避免每个路由重复写 TemplateResponse。"""
    context.setdefault("title", "Site Doctor")
    return templates.TemplateResponse(request=request, name="index.html", context=context)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, url: str | None = None):
    """首页：没有 url 参数时是空表单，有参数时直接诊断并展示结果。"""
    context = {"url_input": url or "", "result": None, "error": None}

    if url and url.strip():
        try:
            report = check_url(url)
            result = score_report(report)
            # 把原始报告和评分合并，模板里一次拿到全部信息
            context["result"] = {
                "report": report,
                "score": result["score"],
                "grade": result["grade"],
                "breakdown": result["breakdown"],
                "suggestions": result["suggestions"],
            }
            context["url_input"] = report["url"]
        except InvalidUrlError as exc:
            context["error"] = str(exc)
        except Exception as exc:  # 兜底：任何意外都不能让页面白屏
            context["error"] = f"诊断过程出现意外错误：{exc}"

    return _render(request, context)
