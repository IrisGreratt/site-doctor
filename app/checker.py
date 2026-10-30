"""诊断引擎：输入网址，检查 HTTP 状态、响应耗时、SSL 证书、安全响应头。

核心函数 check_url(url) 返回一个 dict（报告），供后续评分模块使用。
设计原则：
1. 永远不因为某个单项失败而整体崩溃 —— 单项失败写进 error，其他项照常检测；
2. 网络请求必须有超时上限，不能让一个无响应的站点卡住整个服务；
3. (v3) HTTP 请求与 SSL 检测并行执行 —— 两者互不依赖，串行纯属浪费时间。
"""

import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from app import cache


# 请求超时上限（秒）：连接 + 读取各 5 秒
DEFAULT_TIMEOUT = 5.0
# 浏览器常用的 User-Agent，避免部分站点直接拒绝空 UA
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 重点检查的安全响应头：header 名 -> 中文说明
SECURITY_HEADERS = {
    "strict-transport-security": "HSTS 强制 HTTPS",
    "content-security-policy": "内容安全策略 CSP",
    "x-content-type-options": "禁止 MIME 类型嗅探",
    "x-frame-options": "防点击劫持",
    "referrer-policy": "Referrer 策略",
}


class InvalidUrlError(ValueError):
    """网址格式不合法（例如没有域名、协议不支持）。"""


def normalize_url(url: str) -> str:
    """把用户输入整理成合法 URL：去空格、缺协议时默认补 https://。"""
    if url is None:
        raise InvalidUrlError("网址不能为空")

    url = url.strip()
    if not url:
        raise InvalidUrlError("网址不能为空")

    # 没写协议时默认补 https
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidUrlError(f"不支持的协议：{parsed.scheme}，只支持 http / https")
    if not parsed.hostname:
        raise InvalidUrlError(f"无法从「{url}」中解析出域名")

    return url


def check_ssl(hostname: str, port: int = 443, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """检查 SSL 证书：返回是否有效、过期时间、剩余天数、颁发者。

    坑：设置 verify_mode=CERT_NONE 时，Python 的 ssl 模块返回的证书是空 dict。
    所以这里分两次握手：
      1) 先用「正常验证」握手 —— 绝大多数站点会走这条路，能拿到完整证书字段；
      2) 如果验证失败（如自签名/过期证书），再降级为不验证，并用 binary_form
         配合 cryptography 解析，拿到过期时间做判断。
    """
    # --- 第一次：正常验证握手 ---
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as tls_sock:
                cert = tls_sock.getpeercert()
        return _cert_to_result(cert, verified=True)
    except (socket.timeout, socket.gaierror) as exc:
        return {"ok": False, "error": f"连接失败：{exc}"}
    except ssl.SSLError as verify_error:
        # 验证失败，进入降级探测
        pass
    except OSError as exc:
        return {"ok": False, "error": f"网络错误：{exc}"}

    # --- 第二次：降级握手，仅用于拿到过期时间 ---
    fallback = ssl.create_default_context()
    fallback.check_hostname = False
    fallback.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with fallback.wrap_socket(sock, server_hostname=hostname) as tls_sock:
                der = tls_sock.getpeercert(binary_form=True)
    except OSError as exc:
        return {"ok": False, "error": f"证书验证失败且无法读取：{exc}"}

    if not der:
        return {"ok": False, "error": f"证书验证未通过：{verify_error}"}

    result = _parse_der_cert(der)
    result["ok"] = False
    result["error"] = f"证书验证未通过：{verify_error}"
    return result


def _parse_der_cert(der: bytes) -> dict:
    """用 cryptography 解析二进制证书，取出过期时间与颁发者。"""
    try:
        from cryptography import x509
    except ImportError:  # 没装 cryptography 时退化
        return {
            "ok": False,
            "error": "证书验证未通过（缺少 cryptography 库，无法读取过期时间）",
        }

    cert = x509.load_der_x509_certificate(der)
    not_after = cert.not_valid_after_utc
    days_left = (not_after - datetime.now(timezone.utc)).days
    issuer = cert.issuer
    org = issuer.get_attributes_for_oid(
        x509.oid.NameOID.ORGANIZATION_NAME
    )
    common = issuer.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
    issuer_name = (
        org[0].value if org else (common[0].value if common else "未知")
    )
    return {
        "ok": True,
        "days_left": days_left,
        "expires_at": not_after.strftime("%Y-%m-%d"),
        "issuer": issuer_name,
    }


def _cert_to_result(cert: dict, verified: bool) -> dict:
    """把 ssl 模块返回的证书 dict 转成统一的结果结构。"""
    not_after_str = cert.get("notAfter", "")
    try:
        not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return {"ok": False, "error": f"无法解析证书过期时间：{not_after_str}"}

    days_left = (not_after - datetime.now(timezone.utc)).days

    # issuer 是 (('commonName', 'R3'), ('organizationName', "Let's Encrypt"), ...) 的嵌套元组
    issuer_fields = dict(item for field in cert.get("issuer", ()) for item in field)

    return {
        "ok": True,
        "days_left": days_left,
        "expires_at": not_after.strftime("%Y-%m-%d"),
        "issuer": issuer_fields.get("organizationName")
        or issuer_fields.get("commonName")
        or "未知",
    }


def check_headers(response_headers) -> dict:
    """检查安全响应头。httpx 的 Headers 对象大小写不敏感，直接 get 即可。"""
    results = {}
    for header, label in SECURITY_HEADERS.items():
        value = response_headers.get(header)
        results[header] = {"present": value is not None, "label": label, "value": value}
    return results


def _fetch_http(normalized: str, timeout: float) -> dict:
    """发起 HTTP 请求，返回状态码、耗时、响应头和错误信息。

    单独抽成函数是为了能被丢进线程池并发执行。
    注意耗时只统计真正的请求时间，不含线程调度开销。
    """
    result = {
        "reached": False,
        "status_code": None,
        "elapsed_ms": None,
        "headers": {},
        "final_url": None,
        "error": None,
    }

    started = time.perf_counter()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = client.get(normalized)
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["status_code"] = response.status_code
        result["reached"] = True
        result["headers"] = check_headers(response.headers)
        result["final_url"] = str(response.url)
    except httpx.TimeoutException:
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["error"] = f"请求超时（超过 {timeout} 秒）"
    except httpx.HTTPError as exc:
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["error"] = f"请求失败：{exc}"

    return result


def check_url(url: str, timeout: float = DEFAULT_TIMEOUT, use_cache: bool = True) -> dict:
    """诊断一个网址，返回结构化报告。

    v3 改动：
    1. HTTP 请求与 SSL 证书检测改为**并行**。两者都只依赖域名，彼此没有先后
       关系，串行执行等于白白多等一倍时间；
    2. 增加 TTL 缓存，同一网址 60 秒内直接复用上次结果（报告里带 from_cache 标记）。

    返回值结构：
    {
        "url": "https://example.com",
        "reached": True,          # 是否成功完成 HTTP 请求
        "status_code": 200,
        "elapsed_ms": 123.4,
        "ssl": {...},             # check_ssl 的结果；http 站点为 {"ok": False, "error": "非 HTTPS 站点"}
        "headers": {...},         # check_headers 的结果
        "error": None,            # 整体失败时的错误说明
        "from_cache": False       # 是否来自缓存
    }
    """
    normalized = normalize_url(url)

    # 1) 先查缓存
    if use_cache:
        cached = cache.get(normalized)
        if cached is not None:
            cached["from_cache"] = True
            return cached

    parsed = urlparse(normalized)
    is_https = parsed.scheme == "https"
    need_ssl = is_https and bool(parsed.hostname)

    # 2) 两个互不依赖的任务并行执行
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="check") as pool:
        http_future = pool.submit(_fetch_http, normalized, timeout)
        ssl_future = (
            pool.submit(check_ssl, parsed.hostname, 443, timeout) if need_ssl else None
        )

        http_result = http_future.result()
        ssl_result = ssl_future.result() if ssl_future else None

    report = {
        "url": normalized,
        "reached": http_result["reached"],
        "status_code": http_result["status_code"],
        "elapsed_ms": http_result["elapsed_ms"],
        "ssl": ssl_result
        if ssl_result is not None
        else {"ok": False, "error": "非 HTTPS 站点，未启用证书"},
        "headers": http_result["headers"],
        "error": http_result["error"],
        "final_url": http_result["final_url"],
        "from_cache": False,
    }

    # 3) 只缓存成功的结果
    if use_cache and cache.is_cacheable(report):
        cache.put(normalized, report)

    return report
