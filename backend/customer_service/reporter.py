"""
customer_service/reporter.py — CS Reporter 节点

CS Graph 内部的最终回复生成器。
职责: 组装 Expert 结构化输出 → 安全审查 → 格式化用户回复。
不做: 不执行业务逻辑、不操作 DB、不决策。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §12.1
"""
from __future__ import annotations

from typing import Any

from backend.shared.logger import logger


def cs_reporter_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Reporter 节点函数

    从 Expert 的结构化输出组装最终回复，统一执行 OutputGuard。
    """
    decision = state.get("supervisor_decision", {})
    expert_result = state.get("last_expert_result", {})
    handoff_state = state.get("handoff_state", "ai_active")

    answer = _assemble_answer(decision, expert_result, handoff_state, state)

    answer = _run_output_guard(answer, state)

    logger.debug("[CS Reporter] final_answer length=%d", len(answer))

    return {
        "final_answer": answer,
        "cs_context": _build_cs_context_snapshot(state),
    }


def _assemble_answer(
    decision: dict,
    expert_result: dict,
    handoff_state: str,
    state: dict,
) -> str:
    """按优先级组装最终回复"""
    next_action = decision.get("next_action", "")

    if next_action == "handoff" and handoff_state != "ai_active":
        return _handoff_intercept_reply(state)

    if next_action == "pending":
        return _pending_confirmation_reply(state)

    response_draft = expert_result.get("response_draft")
    if response_draft:
        evidence = expert_result.get("evidence")
        if evidence:
            return _format_with_evidence(response_draft, evidence)
        return response_draft

    action_result = expert_result.get("action_result")
    if action_result:
        return _format_action_result(action_result)

    # 专家执行失败：把 error 映射成可操作的提示（2026-09-15 修复）
    # 此前直接落到 _summarize_expert_results 的泛化占位语（"正在处理中，
    # 请稍候。"），用户既不知道失败原因也无从下手——实测匿名用户请求退款时
    # 得到的就是这句话。
    if expert_result.get("status") == "failed" or expert_result.get("error"):
        return _failed_expert_reply(expert_result)

    return _summarize_expert_results(state)


def _failed_expert_reply(expert_result: dict) -> str:
    """把专家失败原因映射为用户可读、可操作的话术（不泄漏技术细节）。"""
    raw = str(expert_result.get("error") or "")
    low = raw.lower()

    if "authentication" in low or "user_id missing" in low or "anonymous" in low:
        return (
            "这项操作需要先确认您的身份，暂时无法为您执行。\n\n"
            "- 请登录后重试（登录后可自助办理）\n"
            "- 或回复「转人工」，由客服协助处理"
        )
    if "permission" in low or "forbidden" in low or "无权限" in raw:
        return "我暂时没有权限为您执行这项操作，已记录您的问题。回复「转人工」可由客服人工处理。"
    if "timeout" in low or "timed out" in low:
        return "系统当前响应较慢，这次没能完成处理。请稍后重试，或回复「转人工」由客服协助。"
    if expert_result.get("status") == "failed":
        return "抱歉，处理您的请求时遇到了问题。请稍后重试，或回复「转人工」由客服协助处理。"
    # 有 error 字段但状态非 failed：给一句可操作的通用兜底（不再用占位语）
    return "抱歉，我暂时没能完成这项处理。可以补充更多信息后重试，或回复「转人工」由客服协助。"


def _handoff_intercept_reply(state: dict) -> str:
    """handoff 拦截场景的固定话术"""
    handoff_state = state.get("handoff_state", "")
    if handoff_state == "human_active":
        return "您好，当前已有客服人员正在处理您的问题，请耐心等待。"
    if handoff_state == "waiting_human":
        return "您好，已为您转接人工客服，正在排队中，请稍候。"
    return "您好，正在为您转接人工客服，请稍候。"


def _pending_confirmation_reply(state: dict) -> str:
    """等待用户确认的回复"""
    pending_action = state.get("pending_action")
    if pending_action and pending_action.get("proposal_text"):
        return pending_action["proposal_text"]
    return "请确认是否继续执行此操作？回复「确认」继续，或「取消」放弃。"


def _format_with_evidence(draft: str, evidence: list[dict]) -> str:
    """知识问答场景: 在回复草稿后附加引用来源"""
    if not evidence:
        return draft

    lines = [draft, "", "---", "参考来源:"]
    for i, ev in enumerate(evidence, 1):
        source = ev.get("source", ev.get("doc_id", "未知来源"))
        lines.append(f"{i}. {source}")
    return "\n".join(lines)


def _format_action_result(action: dict) -> str:
    """业务动作场景: 格式化执行结果"""
    status = action.get("status", "unknown")
    detail = action.get("detail", "")

    if status == "success":
        prefix = "操作已成功执行。"
    elif status == "pending_confirmation":
        prefix = "请确认以下操作:"
    elif status == "failed":
        prefix = "操作执行失败。"
    else:
        prefix = "操作状态未知。"

    if detail:
        return f"{prefix}\n{detail}"
    return prefix


def _summarize_expert_results(state: dict) -> str:
    """降级: 基于可用信息生成总结"""
    cs_route = state.get("cs_route", {})
    domain = cs_route.get("domain", "")
    user_message = state.get("user_message", "")

    expert_result = state.get("last_expert_result", {})
    data = expert_result.get("data")
    if data and isinstance(data, dict):
        summary = data.get("summary", "")
        if summary:
            return summary

    if domain == "KNOWLEDGE":
        return "抱歉，暂时无法找到相关信息。建议您转接人工客服获取更详细的帮助。"

    return f"已收到您的问题: \"{user_message[:50]}\"。正在处理中，请稍候。"


def _run_output_guard(answer: str, state: dict) -> str:
    """执行 OutputGuard 安全审查"""
    try:
        from backend.customer_service.security.output_guard import get_output_guard
        result = get_output_guard().check(answer, state.get("cs_context"))
        return result.text
    except Exception:
        logger.warning("[CS Reporter] OutputGuard failed, returning raw answer")
        return answer


def _build_cs_context_snapshot(state: dict) -> dict:
    """构建 CS 上下文快照，回传给 Main Graph"""
    inner_ctx = state.get("cs_context", {}) or {}
    return {
        "conversation_id": state.get("conversation_id", ""),
        "handoff_state": state.get("handoff_state", ""),
        "confirmation_state": state.get("confirmation_state", ""),
        "cs_route": state.get("cs_route", {}),
        "expert_history": state.get("expert_history", []),
        "supervisor_decision": state.get("supervisor_decision", {}),
        # 投诉工单幂等标记（complaint 专家防重入）
        "complaint_ticket_id": inner_ctx.get("complaint_ticket_id", ""),
        "complaint_severity": inner_ctx.get("complaint_severity", ""),
    }
