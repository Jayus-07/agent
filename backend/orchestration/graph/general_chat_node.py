"""general_chat_node.py — 寒暄/能力咨询主 LLM 直答节点（路由入口重构 2026-09-22）

架构位置：
    Guard → Context Assembler → ContinuationResolver → Coarse Domain Router
        → general_chat（本节点，直连主 LLM → END）

解决：问候/能力咨询此前进主图 plan 支线，白跑 planner/critique/supervisor
（多条 LLM 调用），甚至被 RAG 检索出拒答话术。现在：
  - 不进 planner/supervisor/任何 Skill（零工具调用）；
  - 不进 RAG 检索（知识库拒答话术对寒暄是纯噪声）；
  - 复用 L1 会话历史（memory.start_session 产出的 messages）保持语气连贯；
  - 流式走既有 proxy sink（与 reporter 同一模式），失败降级静态话术。
"""
from __future__ import annotations

from backend.shared.logger import logger

# 系统提示词已收编进 prompt 注册表（key=general_chat.system，2026-10-06）：
# 运行时经 PromptService 渲染（版本治理/trace 记账/请求级 pin）；
# 常量保留为注册表不可用时的降级兜底（与 YAML default 逐字一致，
# 漂移守卫见 tests/prompts/test_bare_prompt_collection.py）。
_SYSTEM_PROMPT = (
    "你是企业智能运营助手。用户在打招呼、问候或询问你的能力。"
    "自然、简洁地回应（不超过 150 字），并简要介绍你能帮忙的事情："
    "旅游行程规划、订单与售后客服、商品智能选品、经营数据查询与分析、"
    "知识库问答。不要编造数据，不要调用任何工具。"
)


def _chat_system_prompt() -> str:
    """系统提示词：注册表优先，异常降级模块常量（软失败）。"""
    try:
        from backend.prompts.service import prompt_service

        return prompt_service.render_sync("general_chat.system").text
    except Exception as exc:  # noqa: BLE001 — 寒暄路径不因 prompt 读取失败报错
        logger.warning(f"[GeneralChat] 注册表渲染失败，降级内置常量: {exc}")
        return _SYSTEM_PROMPT

_FALLBACK_ANSWER = (
    "你好！我是企业智能运营助手，可以帮你规划旅游行程、处理订单售后、"
    "做商品智能选品、查询与分析经营数据，也能回答知识库里的制度问题。"
    "请问今天想从哪件事开始？"
)


def general_chat_node(state: dict) -> dict:
    """寒暄直答：主 LLM 生成，不调用任何工具/Skill/RAG。"""
    question = (state.get("question") or "").strip()
    try:
        from backend.config import ENABLE_TOKEN_STREAMING
        from backend.infra.llm import llm
        from backend.infra.llm.proxy import (
            emit_stream_delta,
            extract_chunk_reasoning,
            extract_chunk_text,
        )
        from langchain_core.messages import AIMessage

        # 历史只取最近几条（寒暄不需要深上下文；防 prompt 膨胀）
        history = [
            m for m in (state.get("messages") or [])
            if getattr(m, "content", None)
        ][-6:]
        msgs = [("system", _chat_system_prompt()), *history]
        if question:
            msgs.append(("human", question))

        if ENABLE_TOKEN_STREAMING:
            parts: list[str] = []
            for chunk in llm.stream(msgs):
                reasoning = extract_chunk_reasoning(chunk)
                if reasoning:
                    emit_stream_delta(reasoning, kind="thinking")
                text = extract_chunk_text(chunk)
                if text:
                    parts.append(text)
                    emit_stream_delta(text)
            resp = AIMessage(content="".join(parts))
        else:
            resp = llm.invoke(msgs)

        answer = (resp.content or "").strip() if hasattr(resp, "content") else str(resp).strip()
        if not answer:
            raise ValueError("empty answer")
        logger.info(f"[GeneralChat] 直答完成: {len(answer)} 字符")
        return {"final_answer": answer}
    except Exception as e:
        logger.warning(f"[GeneralChat] 主 LLM 直答失败，降级静态话术: {e}")
        return {"final_answer": _FALLBACK_ANSWER}
