"""http_tool_client — HTTP API Tool 的统一出网调用器（Batch 1，ADR-0011 扩展）。

外部 REST API 工具与 MCP 工具共用治理链（GovernanceRuntime → SafeToolExecutor），
本模块只负责"经审核的调用定义（invocation）→ 实际 HTTP 请求"这一层：
  - 参数渲染严格按 invocation 模板：path/query/headers 只允许 {name} 引用
    已声明参数；POST 的 JSON body 取 body_from 指定的参数整体；
  - 未在模板中引用的参数不进请求（防参数走私），缺模板参数显式报错；
  - httpx trust_env=False 直连（与 mcp_client 同模式，防止系统代理架空
    url_guard 的逐 IP SSRF 校验）；目标 URL 的最终校验在调用点由
    skill 层完成（连接登记时已过 url_guard，此处防 DNS rebinding）。
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

from backend.shared.logger import logger

_TEMPLATE_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class HttpToolError(RuntimeError):
    """HTTP API 工具调用失败（连接/超时/上游错误/映射错误统一收口）。"""


def _render_template(value: str, arguments: dict[str, Any]) -> str:
    """渲染 {name} 模板；缺失参数显式报错（不静默替换为空）。"""
    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in arguments or arguments[name] is None:
            raise HttpToolError(f"模板参数缺失: {{{name}}}")
        return str(arguments[name])

    return _TEMPLATE_RE.sub(_sub, str(value))


def _render_query(query: dict[str, str],
                  arguments: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, value in (query or {}).items():
        rendered = _render_template(value, arguments)
        if rendered == "":
            continue  # 显式空串 = 该 query 参数本次不发送
        pairs.append((_render_template(key, arguments), rendered))
    return pairs


def render_path(path: str, arguments: dict[str, Any]) -> str:
    """渲染 path 模板并逐段 URL 编码（防注入 / 保留 / 分隔符）。"""
    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in arguments or arguments[name] is None:
            raise HttpToolError(f"路径参数缺失: {{{name}}}")
        return quote(str(arguments[name]), safe="")

    return _TEMPLATE_RE.sub(_sub, path)


def invoke_http_tool(
    base_url: str,
    invocation: dict[str, Any],
    arguments: dict[str, Any],
    *,
    timeout_s: float,
    headers: dict[str, str] | None = None,
) -> Any:
    """按审核过的 invocation 发起 HTTP 请求并返回归一结果。

    返回 JSON（dict/list）或文本。2xx 之外抛 HttpToolError（含状态码）。
    """
    import httpx

    method = str((invocation or {}).get("method") or "").upper()
    if method not in {"GET", "POST", "PUT"}:
        raise HttpToolError(f"HTTP 工具方法非法: {method!r}")
    args = dict(arguments or {})
    path = render_path(str(invocation.get("path") or ""), args)
    url = f"{str(base_url or '').rstrip('/')}{path}"
    query = _render_query(invocation.get("query") or {}, args)
    req_headers = {
        _render_template(k, args): _render_template(v, args)
        for k, v in (invocation.get("headers") or {}).items()
    }
    for key, value in (headers or {}).items():
        req_headers[key] = value  # 凭据头最后注入，配置面不可覆盖

    json_body = None
    body_from = str(invocation.get("body_from") or "")
    if method in ("POST", "PUT"):
        if body_from:
            if body_from not in args:
                raise HttpToolError(f"body 参数缺失: {body_from}")
            json_body = args[body_from]
        else:
            # 未声明 body_from 时，未被 path/query/headers 模板引用的参数整体入 body
            referenced = set(invocation.get("query") or {}) | set(
                invocation.get("headers") or {})
            referenced.update(
                m.group(1) for m in _TEMPLATE_RE.finditer(
                    str(invocation.get("path") or "")))
            json_body = {k: v for k, v in args.items() if k not in referenced}

    started = time.perf_counter()
    try:
        with httpx.Client(trust_env=False, timeout=timeout_s) as client:
            response = client.request(
                method, url, params=query or None, headers=req_headers or None,
                json=json_body,
            )
    except httpx.TimeoutException as exc:
        raise HttpToolError(
            f"HTTP 工具超时（>{timeout_s}s）: {method} {url}") from exc
    except Exception as exc:  # noqa: BLE001 — 连接层异常统一收口
        raise HttpToolError(
            f"HTTP 工具调用失败: {method} {url} — "
            f"{type(exc).__name__}: {exc}") from exc

    elapsed = int((time.perf_counter() - started) * 1000)
    if response.status_code >= 400:
        raise HttpToolError(
            f"HTTP 工具上游错误 {method} {url}: HTTP {response.status_code}")
    content_type = response.headers.get("content-type", "")
    try:
        payload: Any = (
            response.json() if "json" in content_type else response.text
        )
    except ValueError:
        payload = response.text
    logger.info("[HttpTool] %s %s -> %d (%dms)",
                method, url, response.status_code, elapsed)
    return payload


__all__ = ["HttpToolError", "invoke_http_tool", "render_path"]
