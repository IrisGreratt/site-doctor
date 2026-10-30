"""报告导出：把体检结果生成一份自包含的 HTML 文件。

「自包含」的意思是：样式全部内嵌、不依赖任何外部链接，
下载后直接双击就能打开，发给别人、放进简历附件都能正常显示。
"""

from datetime import datetime
from html import escape

# 分数等级 -> 主色，和网页版保持一致
LEVEL_COLORS = {
    "great": "#10b981",
    "good": "#3b82f6",
    "ok": "#f59e0b",
    "bad": "#ef4444",
}


def _level(score: int) -> str:
    if score >= 90:
        return "great"
    if score >= 75:
        return "good"
    if score >= 60:
        return "ok"
    return "bad"


def _safe_filename(url: str) -> str:
    """把网址变成安全的文件名：去掉协议和非法字符。"""
    name = url.replace("https://", "").replace("http://", "")
    name = name.strip("/").replace("/", "_").replace(":", "_")
    # Windows 文件名非法字符
    for ch in '<>:"\\|?*':
        name = name.replace(ch, "_")
    name = name[:60] or "site"
    return name


def report_filename(url: str) -> str:
    """生成下载用的文件名，例如：www.baidu.com_20261030.html"""
    stamp = datetime.now().strftime("%Y%m%d")
    return f"{_safe_filename(url)}_{stamp}.html"


def build_report_html(result: dict, url: str) -> str:
    """根据诊断结果生成完整的 HTML 文档字符串。

    result 结构：score / grade / breakdown / suggestions / report
    """
    score = result["score"]
    grade = result["grade"]
    level = _level(score)
    color = LEVEL_COLORS[level]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = result.get("report", {})

    # ---- 各项检查的进度条 ----
    rows = []
    for item in result["breakdown"]:
        ratio = int(item["score"] / item["max"] * 100) if item["max"] else 0
        if ratio >= 80:
            bar_color = LEVEL_COLORS["great"]
        elif ratio >= 50:
            bar_color = LEVEL_COLORS["ok"]
        else:
            bar_color = LEVEL_COLORS["bad"]
        rows.append(
            f"""
            <div class="row">
              <div class="row-head">
                <span class="row-name">{escape(item["name"])}</span>
                <span class="row-score">{item["score"]} / {item["max"]}</span>
              </div>
              <div class="track"><div class="fill" style="width:{ratio}%;background:{bar_color}"></div></div>
              <p class="row-detail">{escape(item["detail"])}</p>
            </div>
            """
        )

    # ---- 建议列表 ----
    if result["suggestions"]:
        advices = "".join(
            f"<li>{escape(text)}</li>" for text in result["suggestions"]
        )
        advice_block = f'<ul class="advice">{advices}</ul>'
    else:
        advice_block = '<p class="all-good">🎉 没有发现需要改进的问题。</p>'

    # ---- 基础信息 ----
    status = report.get("status_code")
    elapsed = report.get("elapsed_ms")
    ssl_info = report.get("ssl") or {}

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>网站体检报告 - {escape(url)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: #f2f5f9; color: #1f2937; padding: 40px 16px; line-height: 1.6;
  }}
  .wrap {{ max-width: 720px; margin: 0 auto; }}
  .head {{ text-align: center; margin-bottom: 24px; }}
  .head h1 {{ font-size: 1.5rem; margin-bottom: 6px; }}
  .head p {{ color: #6b7280; font-size: .88rem; word-break: break-all; }}
  .score-card {{
    background: #fff; border-radius: 16px; padding: 30px 24px; text-align: center;
    border-top: 5px solid {color}; box-shadow: 0 4px 16px rgba(0,0,0,.06); margin-bottom: 28px;
  }}
  .score {{ font-size: 3.4rem; font-weight: 700; color: {color}; line-height: 1; }}
  .score-sub {{ color: #6b7280; margin-top: 8px; font-size: .95rem; }}
  .facts {{ display: flex; justify-content: center; gap: 28px; margin-top: 18px; flex-wrap: wrap; }}
  .fact {{ text-align: center; }}
  .fact b {{ display: block; font-size: 1rem; color: #1f2937; }}
  .fact span {{ font-size: .78rem; color: #9ca3af; }}
  h2 {{ font-size: 1.05rem; margin: 24px 0 12px; }}
  .row {{ background: #fff; border-radius: 12px; padding: 14px 18px; margin-bottom: 10px;
          box-shadow: 0 2px 8px rgba(0,0,0,.04); }}
  .row-head {{ display: flex; justify-content: space-between; margin-bottom: 8px; }}
  .row-name {{ font-weight: 600; }}
  .row-score {{ color: #6b7280; font-size: .88rem; }}
  .track {{ height: 8px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }}
  .fill {{ height: 100%; border-radius: 999px; }}
  .row-detail {{ margin-top: 8px; font-size: .83rem; color: #6b7280; }}
  .advice {{ list-style: none; }}
  .advice li {{
    background: #fffbeb; border-left: 4px solid #f59e0b; border-radius: 8px;
    padding: 12px 16px; margin-bottom: 9px; font-size: .9rem;
  }}
  .all-good {{ background: #ecfdf5; border-left: 4px solid #10b981; border-radius: 8px;
               padding: 14px 16px; color: #065f46; }}
  .foot {{ text-align: center; color: #9ca3af; font-size: .78rem; margin-top: 32px; }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="head">
      <h1>🩺 网站体检报告</h1>
      <p>{escape(url)}</p>
    </div>

    <div class="score-card">
      <div class="score">{score}</div>
      <div class="score-sub">/ 100 · {escape(grade)}</div>
      <div class="facts">
        <div class="fact"><b>{status if status is not None else "—"}</b><span>HTTP 状态码</span></div>
        <div class="fact"><b>{f"{elapsed:.0f} ms" if elapsed is not None else "—"}</b><span>响应耗时</span></div>
        <div class="fact"><b>{ssl_info.get("days_left", "—") if ssl_info.get("ok") else "—"}</b><span>证书剩余(天)</span></div>
      </div>
    </div>

    <h2>各项检查</h2>
    {"".join(rows)}

    <h2>改进建议</h2>
    {advice_block}

    <p class="foot">由 Site Doctor 生成 · {now}</p>
  </div>
</body>
</html>"""
