"""provider_probe.py — 供应商连通性分级探测（P1b）

按 `docs/model-config-governance-design.md` B.4 实现四级探测，供管理端 tab②
的「测试图标」调用（已存实例复测 + 草稿态测试）。

| 级 | 动作 | 失败含义 |
|---|---|---|
| L0 | url_guard + TCP/TLS | 地址写错 / 不可达 |
| L1 | `GET {base}/models` | 404 → 可能少 `/v1`，**降级不判死** |
| L2 | 最小 chat 调用（`max_tokens=16`，prompt 固定） | 区分「Key 错」与「模型名错」 |
| L3 | `stream=true` 观察是否回传 usage | 不回传 → `skip`（记账会缺 token 数） |

三条实现约定（都有原因，勿擅改）：

1. **L1 失败不判死，降级继续跑 L2**：大量 coding plan / 中转站不实现 `/models`。
   若判死，用户会遇到「测试不通过但其实能用」，测试按钮从此没人信（B.4 硬约束 1）。
   因此只有 **L0 失败短路**，其余各级失败只记录、继续往下测。
2. **L2/L3 用裸 langchain 客户端调用，不走 `proxy`**：既复用真实客户端栈
   （B.4 硬约束 2），又天然不产生 usage 记录 / 不烧预算 / 不进
   `llm_usage_attribution` —— B.4 硬约束 3 要求的「探测流量排除在统计之外」
   由此**结构性满足**，无需侵入 token_tracker 打标。
3. **原文摘要截断 200 字**：探测结果是排障第一现场，而真因常被埋在 N 层语义
   错误之下（B.4 硬约束 4）。

安全边界（B.6）：私网放行**只**由调用方按 `network_scope == "private"` 显式
传入，绝不可由「解析出来是私网」自动推导 —— 否则 DNS rebinding 直接绕过。
"""
from __future__ import annotations

import asyncio
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from backend.shared.logger import logger
from backend.tools.url_guard import UrlBlockedError, assert_url_allowed

DETAIL_LIMIT = 200

_L0_TIMEOUT = 5.0
_L1_TIMEOUT = 8.0
_L2_TIMEOUT = 20.0

_PROBE_PROMPT = "ping"
_PROBE_MAX_TOKENS = 16

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_DEGRADED = "fail_degraded"
STATUS_SKIP = "skip"

# 失败归因的文本线索（顺序有意义：先判更具体的「模型名」再判「Key」）
_MODEL_ERROR_HINTS = (
    "model not found", "does not exist", "unknown model", "no such model",
    "invalid model", "model_not_found", "not a valid model",
)
_KEY_ERROR_HINTS = (
    "invalid api key", "incorrect api key", "invalid_api_key", "unauthorized",
    "authentication", "api key", "401", "403",
)


# ── 结果结构 ────────────────────────────────────────────────────────────


@dataclass
class ProbeStep:
    """单级探测结果。`status` ∈ pass / fail / fail_degraded / skip。"""

    level: str
    status: str
    summary: str
    detail: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "status": self.status,
            "summary": self.summary,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class ProbeResult:
    """四级探测总结果。`blocked_at` 为**首个**判死级别（None = 全通过）。"""

    ok: bool
    blocked_at: str | None
    steps: list[ProbeStep] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked_at": self.blocked_at,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
        }


def _clip(text: Any, limit: int = DETAIL_LIMIT) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit] + "…（已截断）"


def _step(
    level: str, status: str, summary: str, *, detail: Any = "", t0: float | None = None
) -> ProbeStep:
    ms = int((time.monotonic() - t0) * 1000) if t0 is not None else 0
    return ProbeStep(level=level, status=status, summary=summary,
                     detail=_clip(detail), elapsed_ms=ms)


# ── L0：URL + DNS + TCP/TLS ─────────────────────────────────────────────


def _tcp_tls_ok(host: str, port: int, scheme: str, timeout: float) -> None:
    """建连一次；https 再握一次 TLS（证书问题会在此暴露）。"""
    with socket.create_connection((host, port), timeout=timeout):
        pass
    if scheme == "https":
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host):
                pass


async def probe_l0(base_url: str, *, allow_private: bool) -> ProbeStep:
    t0 = time.monotonic()
    try:
        assert_url_allowed(base_url, allow_private=allow_private)
    except UrlBlockedError as e:
        return _step("L0", STATUS_FAIL, f"地址被安全策略拦截：{e}", t0=t0)

    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if not host:
        return _step("L0", STATUS_FAIL, "URL 缺少 host：检查地址拼写", t0=t0)
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

    try:
        await asyncio.wait_for(
            asyncio.to_thread(_tcp_tls_ok, host, port, parsed.scheme.lower(), _L0_TIMEOUT),
            timeout=_L0_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return _step("L0", STATUS_FAIL,
                     f"连接 {host}:{port} 超时：检查地址、端口与网络", t0=t0)
    except Exception as e:  # noqa: BLE001 — 探测必须把任何异常转成结论
        return _step("L0", STATUS_FAIL,
                     f"不可达（{host}:{port}）：{type(e).__name__}: {e}", t0=t0)

    return _step("L0", STATUS_PASS, f"URL 可达（{host}:{port}，TLS 正常）", t0=t0)


# ── L1：GET {base}/models ───────────────────────────────────────────────


async def probe_l1(
    base_url: str, api_key: str, *, extra_headers: dict[str, str] | None = None
) -> ProbeStep:
    t0 = time.monotonic()
    url = base_url.rstrip("/") + "/models"
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    if extra_headers:
        headers.update(extra_headers)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=_L1_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:  # noqa: BLE001
        return _step("L1", STATUS_DEGRADED,
                     f"端点列表不可用（{type(e).__name__}），已跳过（不影响使用）", t0=t0)

    body = resp.text
    if resp.status_code == 200:
        return _step("L1", STATUS_PASS, "端点响应正常（/models 可用）",
                     detail=body, t0=t0)
    if resp.status_code == 404:
        hint = " —— base_url 可能缺少 /v1 后缀" if "/v1" not in url else ""
        return _step("L1", STATUS_DEGRADED,
                     f"该站点未实现 /models{hint}，已跳过（不影响使用）",
                     detail=body, t0=t0)
    if resp.status_code in (401, 403):
        return _step("L1", STATUS_FAIL,
                     f"Key 被拒绝（HTTP {resp.status_code}）：检查 API Key",
                     detail=body, t0=t0)
    return _step("L1", STATUS_DEGRADED,
                 f"端点返回 HTTP {resp.status_code}，已跳过（不影响使用）",
                 detail=body, t0=t0)


# ── L2：最小 chat 调用（真实客户端栈） ──────────────────────────────────


def build_probe_client(
    driver: str,
    *,
    model_name: str,
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """按 **driver**（协议）构建最小客户端。

    草稿态探测没有 provider id，模型可能也未注册，故只能按 driver 构建 ——
    这正是 B.1「厂商差异不在代码里」的直接推论。内置 provider 的 `build_*`
    与之使用同一组 langchain 客户端与参数（P1a-1 已统一为传参式），差别仅在
    探测固定了 `max_tokens=16` 与 `temperature=0`。

    各客户端**延迟导入**（照 providers/*.py 约定），避免把重依赖拖进导入链。
    """
    d = (driver or "").strip().lower()

    if d == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name, base_url=base_url,
            temperature=0.0, num_predict=_PROBE_MAX_TOKENS,
        )

    if d == "anthropic":
        from langchain_anthropic import ChatAnthropic

        headers = {"x-api-key": api_key}
        headers.update(extra_headers or {})
        return ChatAnthropic(
            model=model_name,
            anthropic_api_key=api_key,
            anthropic_api_url=base_url,
            default_headers=headers,
            max_tokens=_PROBE_MAX_TOKENS,
            timeout=_L2_TIMEOUT,
        )

    if d in ("openai", ""):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            default_headers=dict(extra_headers) if extra_headers else None,
            max_tokens=_PROBE_MAX_TOKENS,
            temperature=0.0,
            request_timeout=_L2_TIMEOUT,
        )

    raise ValueError(f"不支持的驱动: {driver or '(空)'}（可选 openai / anthropic / ollama）")


def _invoke_once(client: Any) -> str:
    from langchain_core.messages import HumanMessage

    resp = client.invoke([HumanMessage(content=_PROBE_PROMPT)])
    content = getattr(resp, "content", resp)
    return content if isinstance(content, str) else str(content)


def _classify_l2_failure(exc: Exception) -> str:
    """把 L2 异常归因到「Key 错」或「模型名错」—— 用户最需要分清的一步。"""
    raw = f"{type(exc).__name__}: {exc}"
    low = raw.lower()
    if any(h in low for h in _MODEL_ERROR_HINTS):
        return f"模型名错误或该 Key 无权访问该模型 —— {_clip(raw)}"
    if any(h in low for h in _KEY_ERROR_HINTS):
        return f"Key 无效或无权访问 —— {_clip(raw)}"
    return f"最小调用失败：{_clip(raw)}"


async def probe_l2(
    driver: str,
    *,
    model_name: str,
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str] | None = None,
) -> ProbeStep:
    t0 = time.monotonic()
    if not model_name:
        return _step("L2", STATUS_FAIL, "未提供模型名，无法验证模型可用性", t0=t0)

    try:
        client = build_probe_client(
            driver, model_name=model_name, api_key=api_key,
            base_url=base_url, extra_headers=extra_headers,
        )
    except Exception as e:  # noqa: BLE001
        return _step("L2", STATUS_FAIL,
                     f"客户端构建失败：{type(e).__name__}: {e}", t0=t0)

    try:
        content = await asyncio.wait_for(asyncio.to_thread(_invoke_once, client),
                                         timeout=_L2_TIMEOUT)
    except asyncio.TimeoutError:
        return _step("L2", STATUS_FAIL,
                     f"最小调用超时（{int(_L2_TIMEOUT)}s）：模型可能不可用或响应过慢",
                     t0=t0)
    except Exception as e:  # noqa: BLE001
        return _step("L2", STATUS_FAIL, _classify_l2_failure(e), t0=t0)

    return _step("L2", STATUS_PASS, "模型名可用（已用真实客户端调用）",
                 detail=f"返回：{content}", t0=t0)


# ── L3：流式 usage 回传 ─────────────────────────────────────────────────


def _stream_usage_seen(client: Any) -> tuple[bool, str]:
    from langchain_core.messages import HumanMessage

    seen = False
    text = ""
    for chunk in client.stream([HumanMessage(content=_PROBE_PROMPT)]):
        if getattr(chunk, "usage_metadata", None):
            seen = True
        piece = getattr(chunk, "content", "")
        if isinstance(piece, str):
            text += piece
    return seen, text


async def probe_l3(
    driver: str,
    *,
    model_name: str,
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str] | None = None,
) -> ProbeStep:
    """不判死：只用来决定「记账是否可能缺 token 数」，失败一律 skip。"""
    t0 = time.monotonic()
    try:
        client = build_probe_client(
            driver, model_name=model_name, api_key=api_key,
            base_url=base_url, extra_headers=extra_headers,
        )
        seen, text = await asyncio.wait_for(
            asyncio.to_thread(_stream_usage_seen, client), timeout=_L2_TIMEOUT
        )
    except Exception as e:  # noqa: BLE001
        return _step("L3", STATUS_SKIP,
                     f"流式探测未完成（{type(e).__name__}）：不影响可用性",
                     t0=t0)

    if seen:
        return _step("L3", STATUS_PASS, "流式响应回传 usage（记账完整）",
                     detail=text, t0=t0)
    return _step("L3", STATUS_SKIP,
                 "流式不回传 usage —— 记账会缺 token 数（不影响可用性）",
                 detail=text, t0=t0)


# ── 入口 ────────────────────────────────────────────────────────────────


async def probe_provider(
    *,
    driver: str,
    base_url: str,
    api_key: str,
    model_name: str,
    network_scope: str = "public",
    extra_headers: dict[str, str] | None = None,
) -> ProbeResult:
    """执行四级探测。

    只有 **L0 失败短路**（地址不通时后续无意义）；L1 的 404/401 等**都不判死**，
    继续用 L2 拿到更准确的结论（B.4 硬约束 1）。
    """
    allow_private = (network_scope or "public").strip().lower() == "private"
    steps: list[ProbeStep] = []

    l0 = await probe_l0(base_url, allow_private=allow_private)
    steps.append(l0)
    if l0.status == STATUS_FAIL:
        return ProbeResult(ok=False, blocked_at="L0", steps=steps,
                           summary=f"未通过（卡在 L0）：{l0.summary}")

    l1 = await probe_l1(base_url, api_key, extra_headers=extra_headers)
    steps.append(l1)

    l2 = await probe_l2(driver, model_name=model_name, api_key=api_key,
                        base_url=base_url, extra_headers=extra_headers)
    steps.append(l2)

    if l2.status != STATUS_PASS:
        logger.info(
            "[ProviderProbe] 探测未通过：driver=%s target=%s scope=%s blocked=L2",
            driver, base_url, "private" if allow_private else "public",
        )
        return ProbeResult(ok=False, blocked_at="L2", steps=steps,
                           summary=f"未通过（卡在 L2）：{l2.summary}")

    l3 = await probe_l3(driver, model_name=model_name, api_key=api_key,
                        base_url=base_url, extra_headers=extra_headers)
    steps.append(l3)

    logger.info(
        "[ProviderProbe] 探测通过：driver=%s target=%s scope=%s 耗时=%dms",
        driver, base_url, "private" if allow_private else "public",
        sum(s.elapsed_ms for s in steps),
    )
    return ProbeResult(ok=True, blocked_at=None, steps=steps,
                       summary="厂商连通性通过")
