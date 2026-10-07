"""llm_runtime.py — 语义理解层 LLM 调用公共边界。

统一保证（任务书 §二十二/§二十三/§二十六/§二十七）：
  - Prompt 走 PromptService（render_prompt），常量不进生产路径
  - 调用走 backend.infra.llm llm 代理（限流/韧性/llm_usage 记账，
    component 经 trace.tags.cs_target 自动归因 customer_service）
  - PII 掩码进模型（E4b 口径：掩码后不还原）
  - 线程级限时（config={"timeout"} 实测不生效是既有结论）
  - 一次调用失败立即返回 None，**禁止自动多次 retry**（§二十三）；
    降级由调用方走规则路径，绝不 500/断流
"""
from __future__ import annotations

import time

from backend.shared.logger import logger


def llm_invoke_once(prompt: str, timeout_ms: int) -> str | None:
    """单次 LLM 调用：掩码文本 → 模型 → 文本；任何失败返回 None。

    返回 None 的语义 = 「本次语义增强不可用，调用方走规则降级」，
    不区分超时/异常/空响应（统一计数 error 后由调用方细分记录）。
    """
    if not prompt:
        return None
    try:
        from langchain_core.messages import HumanMessage

        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm import llm  # 代理：限流/韧性/llm_usage 记账

        t0 = time.monotonic()
        response = sync_call_with_timeout(
            llm.invoke, timeout_ms / 1000.0, [HumanMessage(content=prompt)],
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        text = str(response.content or "").strip()
        if not text:
            logger.warning("[CS Understanding LLM] 空响应 (elapsed=%dms)", elapsed_ms)
            return None
        logger.info("[CS Understanding LLM] ok elapsed_ms=%d", elapsed_ms)
        return text
    except Exception as exc:  # noqa: BLE001 — 语义层失败必须软降级
        logger.warning("[CS Understanding LLM] 调用失败（走规则降级）: %s", exc)
        return None


def render_understanding_prompt(key: str, **variables: str) -> str | None:
    """渲染语义层 Prompt；PromptService 不可用返回 None（降级=不调 LLM）。"""
    try:
        from backend.customer_service.prompting import render_prompt

        return render_prompt(key, **variables)
    except Exception as exc:  # noqa: BLE001 — prompt 链故障按无 LLM 处理
        logger.warning("[CS Understanding LLM] prompt 渲染失败 (%s): %s", key, exc)
        return None


def mask_for_llm(text: str) -> str:
    """PII 掩码（掩码后不还原——语义理解只需意图与指称结构）。"""
    try:
        from backend.customer_service.pii import mask_pii

        masked, _vault = mask_pii(text or "")
        return masked
    except Exception:  # noqa: BLE001 — 掩码失败按原文不进模型处理
        return ""


def tag_trace(**tags: object) -> None:
    """低基数 trace 打点（软失败：trace 不可达不影响主链路）。"""
    try:
        from backend.observability.tracer import trace_collector

        tracer = trace_collector.current()
        if tracer is None:
            return
        for key, value in tags.items():
            if value not in (None, ""):
                tracer.tags[key] = value
    except Exception:  # noqa: BLE001 — 观测旁路
        pass
