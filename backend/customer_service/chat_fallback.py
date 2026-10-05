"""customer_service/chat_fallback.py — 非业务对话兜底（寒暄分支，任务卡 T3）。

定位：意图分诊识别为「非业务对话」（寒暄/情绪/闲聊，词表 CHITCHAT_PATTERNS）
的消息，不再误入知识漏斗白烧检索，而是走**一次 LLM 人设生成**——
替代的是"必然发生的生硬拒答"，量由分诊规则先行收窄（业务消息全分走）。

边界（V3/V5 验收口径）：
  - 进 LLM 前 PII 脱敏（与知识问答同边界，pii.mask_pii），回复后还原；
  - 人设红线（prompt 内硬约束）：不编造业务事实、不承诺、不出域；
  - 单次调用线程级限时（sync_call_with_timeout，supervisor L3 同款结论：
    config={"timeout"} 在当前 ChatOpenAI 版本实测不生效）；
  - LLM 故障 → 固定友好话术兜底（fail-open，寒暄不该报错给用户）。

接线：cs_supervisor 分诊「非业务对话」出口（任务卡 T6，混线解锁后）；
本模块当前可独立测试与手动调用。

Prompt 治理说明：人设 prompt 已注册进 prompt 注册表（key=
customer_service.chat_fallback，2026-10-06 补齐断线——此前 YAML 存在但
registry 无注册项，loader 启动告警跳过，运行时恒走常量降级）；常量保留
为注册表不可用时的降级兜底（内容逐字一致，守卫测试锁定）。
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.config.customer_service import CS_CHAT_LLM_TIMEOUT_MS
from backend.shared.logger import logger

# 人设 system prompt（E5 收尾，2026-10-05）：权威 = prompt 注册表
# customer_service.chat_fallback（版本治理/热更随 Prompt 发布链路）；
# 常量保留为注册表不可用时的降级兜底（内容逐字一致）。
PERSONA_SYSTEM_PROMPT = """你是电商店铺的客服助手，用口语化中文和用户聊天。
- 简短：一两句话回应完，不写小作文，不堆礼貌用语
- 先接情绪再说话：用户道谢就自然回应，用户着急就先安抚
- 红线（必须遵守）：
  · 不编造任何政策/订单/售后事实——业务问题引导用户直接问
  · 不做任何承诺（赔偿/时效/补偿）
  · 不回答购物客服以外的话题"""


def _persona_system_prompt() -> str:
    """人设 prompt：注册表优先，异常降级模块常量（软失败）。"""
    try:
        from backend.customer_service.prompting import render_prompt
        rendered = render_prompt("customer_service.chat_fallback")
        return rendered or PERSONA_SYSTEM_PROMPT
    except Exception:
        return PERSONA_SYSTEM_PROMPT

# LLM 故障时的固定兜底话术（fail-open：寒暄路径不向用户暴露错误）
_FALLBACK_REPLY = "我在的～有什么购物相关的问题（订单、退款、物流）随时问我。"

_MAX_REPLY_CHARS = 500  # 人设要求简短，超长截断兜底（防模型跑飞）


@dataclass
class ChatFallbackResult:
    reply: str
    pii_masked: bool = False
    error: str | None = None  # 非 None = LLM 走了固定兜底话术


def chat_fallback_enabled() -> bool:
    # 函数内 import：读 config 模块属性而非顶层值拷贝——monkeypatch 测试
    # 与 env 热改重启进程生效两态都正确（与 CS_WINDOW_STANDALONE 同约定）
    from backend.config.customer_service import CS_CHAT_FALLBACK_ENABLED
    return CS_CHAT_FALLBACK_ENABLED


def run_chat_fallback(
    question: str,
    history: list[tuple[str, str]] | None = None,
) -> ChatFallbackResult | None:
    """非业务对话生成。返回 None = 开关关闭（调用方落回旧漏斗路径）。"""
    from backend.config.customer_service import CS_CHAT_FALLBACK_ENABLED
    if not CS_CHAT_FALLBACK_ENABLED:
        return None
    question = (question or "").strip()
    if not question:
        return ChatFallbackResult(reply=_FALLBACK_REPLY)

    from backend.customer_service.pii import mask_pii, unmask_text

    masked_question, vault = mask_pii(question)

    try:
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
        )

        from backend.infra.async_utils import sync_call_with_timeout
        # 必须经 llm 代理调用（G7/V3 记账口径）：get_llm() 返回裸实例，
        # 绕过 proxy 的限流/韧性链/llm_usage 记账——实机验收抓到寒暄调用
        # 零记账后改走代理（与全平台计量同一路径）。
        from backend.infra.llm import llm as llm_proxy

        messages: list = [SystemMessage(content=_persona_system_prompt())]
        for role, text in (history or [])[-4:]:  # 最多带最近 4 轮上下文
            messages.append(
                AIMessage(content=text) if role == "ai" else HumanMessage(content=text))
        messages.append(HumanMessage(content=masked_question))

        timeout_s = CS_CHAT_LLM_TIMEOUT_MS / 1000.0
        response = sync_call_with_timeout(
            llm_proxy.invoke, timeout_s, messages,
        )
        reply = str(response.content).strip()[:_MAX_REPLY_CHARS]
        if not reply:
            return ChatFallbackResult(reply=_FALLBACK_REPLY, pii_masked=True)
        return ChatFallbackResult(
            reply=unmask_text(reply, vault), pii_masked=True)
    except Exception as exc:
        logger.warning("[ChatFallback] LLM 生成失败，走固定兜底话术: %s", exc)
        return ChatFallbackResult(reply=_FALLBACK_REPLY, pii_masked=True,
                                  error=str(exc))
