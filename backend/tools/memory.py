"""tools/memory.py — 长期记忆读写工具

此前记忆子系统（L3 pgvector 长期记忆）只在会话前后由框架自动触发，
Agent 无法主动"记一下这个偏好/查一下用户说过什么"。这里把
MemoryService.search 与 LongTermMemory.store_single 封装为 LangChain tool，
用户/会话身份来自 tools.session 的请求上下文 ContextVar。

工具运行在 Skill 的 asyncio.to_thread 线程里（无 event loop），
异步记忆操作经 MemoryManager.run_tool 提交到记忆专用后台 loop。
"""
from langchain_core.tools import tool
from backend.shared.logger import logger

# 记忆工具输出截断（防长内容挤爆 LLM 上下文）
_MAX_OUTPUT_CHARS = 2000
_ALLOWED_FACT_TYPES = ("user_fact", "preference", "decision", "knowledge")


def _context_ids() -> tuple[str, str, str]:
    """(session_id, user_id, tenant_id) — 无请求上下文时给出可读错误。

    tenant_id 来自网关验签注入的可信身份（ContextVar），禁止模型/请求体
    自报；未声明租户由 normalize_tenant_id 归一为 default（scope 仍精确）。
    """
    from backend.tools.session import _get_session_id, get_tool_tenant_id, get_tool_user_id
    session_id = _get_session_id() or ""
    user_id = get_tool_user_id() or "default"
    tenant_id = get_tool_tenant_id() or ""
    if not session_id:
        raise RuntimeError("无会话上下文（session_id 为空），记忆工具需要在 Agent 请求内调用")
    return session_id, user_id, tenant_id


@tool
def memory_search_tool(query: str, top_k: int = 5) -> str:
    """
    检索当前用户的长期记忆（L3，语义+混合检索）。
    只检索可信请求入口域及 user_global 记忆；业务域来自服务端 RequestContext，
    不接受模型参数选择，以免不同业务域的用户画像被交叉注入。
    适用场景：回答前确认用户的偏好/历史决定/背景事实（如"用户之前提过什么需求"）。
    """
    from backend.memory.manager import memory_manager
    from backend.tools.session import get_tool_domain_hint

    if not query or not query.strip():
        return "❌ 错误: query 不能为空"

    session_id, user_id, tenant_id = _context_ids()
    domain_hint = get_tool_domain_hint()
    try:
        facts = memory_manager.run_tool(
            lambda: memory_manager.service.search(
                query.strip(), session_id, user_id=user_id, top_k=max(1, min(int(top_k), 10)),
                tenant_id=tenant_id, domain=domain_hint,
            )
        )
    except Exception as e:
        logger.error(f"[Tool:memory_search] 检索失败: {e}")
        return f"❌ 记忆检索失败: {e}"

    if not facts:
        return "未找到相关长期记忆。"
    lines = [
        f"- [{f.fact_type}] memory_id={f.memory_id or 'unknown'}"
        f" key={f.memory_key or 'unkeyed'}: {f.content}"
        for f in facts
    ]
    output = f"找到 {len(facts)} 条相关长期记忆:\n" + "\n".join(lines)
    return output[:_MAX_OUTPUT_CHARS]


@tool
def memory_store_tool(content: str, memory_type: str = "user_fact",
                      memory_key: str = "", structured_value: str = "") -> str:
    """
    将重要事实写入当前用户的长期记忆（自动去重 + 冲突裁决 + 覆盖旧值）。
    content: 要记住的事实（一句完整陈述，如"用户偏好看同比而非环比数据"）
    memory_type: user_fact（用户事实）| preference（偏好）| decision（决定）| knowledge（领域知识）
    memory_key: 可选，属性身份（dot 分隔 snake_case，如 project.main_llm /
    response.language）。同一属性的新值会自动替换旧值；无法稳定结构化时留空。
    structured_value: 可选，与 memory_key 成对的规范化属性值（如 doubao、zh）。
    适用场景：用户明确表达偏好/纠正/重要背景时主动记录；不要记录敏感个人信息。
    """
    from backend.config import MEMORY_EXPLICIT_DEFAULT_CONFIDENCE, MEMORY_ORIGIN_EXPLICIT
    from backend.memory.long_term import LongTermMemory, MemoryFact
    from backend.memory.manager import memory_manager
    from backend.memory.pii_filter import scan_and_sanitize
    from backend.memory.repository.memory_repo import MemoryRepository
    from backend.memory.database import AsyncSessionLocal

    if not content or not content.strip():
        return "❌ 错误: content 不能为空"
    if memory_type not in _ALLOWED_FACT_TYPES:
        return (f"❌ 错误: memory_type 必须是 {'/'.join(_ALLOWED_FACT_TYPES)}，"
                f"收到: {memory_type}")

    session_id, user_id, tenant_id = _context_ids()

    # 写入前强制 PII 脱敏（与后台管线同一口径）
    scan = scan_and_sanitize(content.strip())
    # 显式通道 provenance（STOP B）：用户主动要求记住 → origin=explicit、
    # 高置信默认值。tool 请求上下文（ContextVar）当前无 message id，
    # source_message_id 置 NULL（不造假），session 归属仍可追溯。
    # scope（STOP C）：tenant 来自可信 ContextVar；memory_key/structured_value
    # 为模型可选参数（normalize+validate 在 store_with_resolution 入口强制）
    fact = MemoryFact(
        fact_type=memory_type,
        content=scan.sanitized,
        session_id=session_id,
        origin=MEMORY_ORIGIN_EXPLICIT,
        confidence_score=MEMORY_EXPLICIT_DEFAULT_CONFIDENCE,
        source_message_id=None,
        memory_key=(memory_key or "").strip() or None,
        structured_value=(structured_value or "").strip() or None,
    )

    async def _store() -> tuple[bool, str]:
        from backend.memory.keying import StoreOutcome
        async with AsyncSessionLocal() as db:
            result = await LongTermMemory(MemoryRepository(db)).store_with_resolution(
                fact, user_id, session_id, tenant_id)
            await db.commit()
            try:
                from backend.observability.metrics import memory_store_outcome_total
                memory_store_outcome_total.labels(outcome=result.outcome.value).inc()
                if result.outcome == StoreOutcome.SUPERSEDED:
                    from backend.observability.metrics import (
                        memory_conflict_total,
                        memory_explicit_total,
                        memory_supersede_total,
                    )
                    memory_supersede_total.inc()
                    memory_conflict_total.inc()
                    memory_explicit_total.inc()
                elif result.outcome == StoreOutcome.CONFLICT_BLOCKED_EXPLICIT:
                    from backend.observability.metrics import memory_conflict_total
                    memory_conflict_total.inc()
                else:
                    from backend.observability.metrics import memory_explicit_total
                    memory_explicit_total.inc()
            except Exception:  # 观测面异常不反噬工具
                pass
            return result.stored, result.outcome.value

    try:
        ok, outcome = memory_manager.run_tool(_store)
    except Exception as e:
        logger.error(f"[Tool:memory_store] 写入失败: {e}")
        return f"❌ 记忆写入失败: {e}"

    if ok:
        suffix = "（已覆盖旧值）" if outcome == "SUPERSEDED" else ""
        keying = f", key={fact.memory_key}" if fact.memory_key else ""
        logger.info(f"[Tool:memory_store] 已写入 {memory_type} (user={user_id}, "
                    f"origin=explicit, outcome={outcome}{keying})")
        return f"✅ 已记住（{memory_type}）{suffix}: {scan.sanitized[:200]}"
    if outcome == "CONFLICT_BLOCKED_EXPLICIT":
        return "⏸ 用户此前已明确设定该属性（explicit），自动推断不能覆盖；如需修改请由用户明确说明。"
    return "⏭ 内容与已有记忆重复或重要性不足，未写入。"


@tool
def memory_forget_tool(memory_id: str) -> str:
    """删除当前登录用户的一条长期记忆。

    必须先通过 memory_search_tool 找到准确条目并取得 memory_id；仅在用户
    明确要求遗忘/删除该记忆时调用。服务端身份、租户和删除屏障由 Memory
    服务校验，删除操作受 Tool Governance 确认策略约束。
    """
    from backend.memory.manager import memory_manager

    if not memory_id or not memory_id.strip():
        return "❌ 错误: memory_id 不能为空"
    _session_id, user_id, tenant_id = _context_ids()
    try:
        result = memory_manager.run_tool(
            lambda: memory_manager.service.delete_profile_memory(
                user_id=user_id,
                tenant_id=tenant_id,
                memory_id=memory_id.strip(),
            )
        )
    except Exception as e:
        logger.error(f"[Tool:memory_forget] 删除失败: {e}")
        return "❌ 记忆删除失败，请稍后重试。"
    if not isinstance(result, dict):
        return "❌ 记忆服务暂不可用，删除未确认成功。"
    if result.get("ok"):
        logger.info(
            f"[Tool:memory_forget] 用户删除记忆 (user={user_id}, "
            f"memory_id={memory_id.strip()})"
        )
        return "✅ 已删除该长期记忆。"
    if result.get("error") == "记忆不存在":
        return "未找到可删除的记忆，或该记忆已被删除。"
    return "❌ 记忆服务暂不可用，删除未确认成功。"


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(memory_search_tool, __file__)
tool_registry.register(memory_store_tool, __file__)
tool_registry.register(memory_forget_tool, __file__)
