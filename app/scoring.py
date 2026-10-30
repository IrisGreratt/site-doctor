"""评分模块：把 check_url 的报告变成 0-100 分 + 中文改进建议。

评分维度（满分 100）：
- 可达性   25 分：站点能否正常响应
- 状态码   10 分：HTTP 2xx / 3xx
- 响应速度 15 分：耗时越快分越满
- HTTPS    25 分：是否启用 HTTPS、证书是否有效、剩余天数
- 安全头   25 分：5 个关键安全响应头，每个 5 分

设计说明：每一维都拆成「扣分项 + 建议文案」，最终建议来自扣分原因，
这样分数和文字永远对得上，避免出现「分低但没说原因」的情况。
"""

# 各维度权重
WEIGHT_AVAILABILITY = 25
WEIGHT_STATUS = 10
WEIGHT_SPEED = 15
WEIGHT_HTTPS = 25
WEIGHT_HEADERS = 25

# 速度评分档位：(平均耗时上限毫秒, 得分占比)
SPEED_TIERS = [
    (300, 1.0),
    (800, 0.8),
    (1500, 0.6),
    (3000, 0.4),
    (5000, 0.2),
]


def _grade(score: int) -> str:
    """把分数翻译成等级，前端展示用。"""
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "及格"
    return "需要改进"


def _score_speed(elapsed_ms: float | None) -> float:
    """按耗时返回 0~1 的得分占比。"""
    if elapsed_ms is None:
        return 0.0
    for limit_ms, ratio in SPEED_TIERS:
        if elapsed_ms <= limit_ms:
            return ratio
    return 0.0


def score_report(report: dict) -> dict:
    """对一份 check_url 报告评分。

    返回：
    {
        "score": 0-100 的整数,
        "grade": "优秀"/"良好"/"及格"/"需要改进",
        "breakdown": [{"name": "可达性", "score": 25, "max": 25, "detail": "..."}],
        "suggestions": ["...", "..."]  # 中文建议，按严重程度排序
    }
    """
    breakdown = []
    suggestions = []
    critical = []  # 严重问题，排在建议最前面

    # ---------- 1) 可达性 ----------
    reached = bool(report.get("reached"))
    if reached:
        breakdown.append(
            {
                "name": "可达性",
                "score": WEIGHT_AVAILABILITY,
                "max": WEIGHT_AVAILABILITY,
                "detail": "站点可正常访问",
            }
        )
    else:
        error = report.get("error") or "未知原因"
        breakdown.append(
            {
                "name": "可达性",
                "score": 0,
                "max": WEIGHT_AVAILABILITY,
                "detail": f"无法访问：{error}",
            }
        )
        critical.append(f"站点无法正常访问（{error}），请先确认域名与网络是否正常。")

    # ---------- 2) 状态码 ----------
    status = report.get("status_code")
    if status is None:
        status_score = 0
        status_detail = "未获取到状态码"
        if reached:
            suggestions.append("未能读取 HTTP 状态码，建议检查服务器响应配置。")
    elif 200 <= status < 300:
        status_score = WEIGHT_STATUS
        status_detail = f"HTTP {status}，正常"
    elif 300 <= status < 400:
        status_score = int(WEIGHT_STATUS * 0.8)
        status_detail = f"HTTP {status}，存在重定向"
    elif status == 404:
        status_score = int(WEIGHT_STATUS * 0.3)
        status_detail = "HTTP 404，页面不存在"
        critical.append("首页返回 404，建议检查服务器路由或静态资源部署是否正确。")
    elif 500 <= status < 600:
        status_score = 0
        status_detail = f"HTTP {status}，服务器错误"
        critical.append(f"服务器返回 {status}，请检查后端日志排查异常。")
    else:
        status_score = int(WEIGHT_STATUS * 0.5)
        status_detail = f"HTTP {status}"
        suggestions.append(f"返回状态码 {status}，建议确认该状态码是否符合预期。")
    breakdown.append(
        {
            "name": "状态码",
            "score": status_score,
            "max": WEIGHT_STATUS,
            "detail": status_detail,
        }
    )

    # ---------- 3) 响应速度 ----------
    elapsed = report.get("elapsed_ms")
    speed_ratio = _score_speed(elapsed)
    speed_score = int(round(WEIGHT_SPEED * speed_ratio))
    if elapsed is None:
        speed_detail = "未测得耗时"
    else:
        speed_detail = f"{elapsed:.0f} ms"
        if speed_ratio < 0.6:
            suggestions.append(
                f"响应耗时 {elapsed:.0f} ms，偏慢。建议开启 gzip 压缩、使用 CDN 或数据库索引来加速。"
            )
    breakdown.append(
        {
            "name": "响应速度",
            "score": speed_score,
            "max": WEIGHT_SPEED,
            "detail": speed_detail,
        }
    )

    # ---------- 4) HTTPS 与证书 ----------
    ssl_info = report.get("ssl") or {}
    url = report.get("url", "")
    is_https = url.startswith("https://")
    if not is_https:
        ssl_score = 0
        ssl_detail = "未使用 HTTPS"
        critical.append("站点未使用 HTTPS，数据传输可能被盗听，建议尽快申请免费证书（如 Let's Encrypt）。")
    elif not ssl_info.get("ok"):
        ssl_score = int(WEIGHT_HTTPS * 0.3)
        ssl_detail = ssl_info.get("error", "证书异常")
        critical.append(f"HTTPS 证书异常：{ssl_detail}")
    else:
        days_left = ssl_info.get("days_left", 0)
        if days_left < 0:
            ssl_score = 0
            ssl_detail = f"证书已过期 {-days_left} 天"
            critical.append("HTTPS 证书已过期，浏览器会拦截访问，请立即续期。")
        elif days_left < 7:
            ssl_score = int(WEIGHT_HTTPS * 0.5)
            ssl_detail = f"证书仅剩 {days_left} 天"
            critical.append(f"证书 {days_left} 天后过期，请立即设置自动续期。")
        elif days_left < 30:
            ssl_score = int(WEIGHT_HTTPS * 0.8)
            ssl_detail = f"证书剩余 {days_left} 天"
            suggestions.append(f"证书将在 {days_left} 天后过期，建议开启自动续期。")
        else:
            ssl_score = WEIGHT_HTTPS
            ssl_detail = f"证书有效，剩余 {days_left} 天（颁发者：{ssl_info.get('issuer', '未知')}）"
    breakdown.append(
        {
            "name": "HTTPS 证书",
            "score": ssl_score,
            "max": WEIGHT_HTTPS,
            "detail": ssl_detail,
        }
    )

    # ---------- 5) 安全响应头 ----------
    headers = report.get("headers") or {}
    if not headers:
        header_score = 0
        header_detail = "未获取到响应头"
        missing_labels = []
    else:
        missing = [info["label"] for info in headers.values() if not info.get("present")]
        present_count = len(headers) - len(missing)
        header_score = int(round(WEIGHT_HEADERS * present_count / len(headers)))
        header_detail = f"已配置 {present_count}/{len(headers)} 个关键安全头"
        missing_labels = missing
    # 每个缺失的头单独给一条建议，方便用户按需补
    for label in missing_labels:
        suggestions.append(f"缺少安全响应头：{label}，建议在服务器或网关中补充。")
    breakdown.append(
        {
            "name": "安全响应头",
            "score": header_score,
            "max": WEIGHT_HEADERS,
            "detail": header_detail,
        }
    )

    total = (
        breakdown[0]["score"]
        + breakdown[1]["score"]
        + breakdown[2]["score"]
        + breakdown[3]["score"]
        + breakdown[4]["score"]
    )
    total = max(0, min(100, total))

    # 严重问题置顶，其余保持原顺序
    ordered_suggestions = critical + suggestions

    return {
        "score": total,
        "grade": _grade(total),
        "breakdown": breakdown,
        "suggestions": ordered_suggestions,
    }
