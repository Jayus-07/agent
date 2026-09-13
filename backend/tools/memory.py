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


def _context_ids() -> tuple[str, str]:
    """(session_id, user_id) — 无请求上下文时给出可读错误。"""
    from backend.tools.session import _get_session_id, get_tool_user_id
    session_id = _get_session_id() or ""
    user_id = get_tool_user_id() or "default"
    if not session_id:
        raise RuntimeError("无会话上下文（session_id 为空），记忆工具需要在 Agent 请求内调用")
    return session_id, user_id


@tool
def memory_search_tool(query: str, top_k: int = 5) -> str:
    """
    检索当前用户的长期记忆（L3，语义+混合检索）。
    输入自然语言查询，返回相关记忆条目列表。
    适用场景：回答前确认用户的偏好/历史决定/背景事实（如"用户之前提过什么需求"）。
    """
    from backend.memory.manager import memory_manager

    if not query or not query.strip():
        return "❌ 错误: query 不能为空"

    session_id, user_id = _context_ids()
    try:
        facts = memory_manager.run_tool(
            lambda: memory_manager.service.search(
                query.strip(), session_id, user_id=user_id, top_k=max(1, min(int(top_k), 10)),
            )
        )
    except Exception as e:
        logger.error(f"[Tool:memory_search] 检索失败: {e}")
        return f"❌ 记忆检索失败: {e}"

    if not facts:
        return "未找到相关长期记忆。"
    lines = [f"- [{f.fact_type}] {f.content}" for f in facts]
    output = f"找到 {len(facts)} 条相关长期记忆:\n" + "\n".join(lines)
    return output[:_MAX_OUTPUT_CHARS]


@tool
def memory_store_tool(content: str, memory_type: str = "user_fact") -> str:
    """
    将重要事实写入当前用户的长期记忆（自动去重 + 覆盖旧值）。
    content: 要记住的事实（一句完整陈述，如"用户偏好看同比而非环比数据"）
    memory_type: user_fact（用户事实）| preference（偏好）| decision（决定）| knowledge（领域知识）
    适用场景：用户明确表达偏好/纠正/重要背景时主动记录；不要记录敏感个人信息。
    """
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

    session_id, user_id = _context_ids()

    # 写入前强制 PII 脱敏（与后台管线同一口径）
    scan = scan_and_sanitize(content.strip())
    fact = MemoryFact(fact_type=memory_type, content=scan.sanitized, session_id=session_id)

    async def _store() -> bool:
        async with AsyncSessionLocal() as db:
            ok = await LongTermMemory(MemoryRepository(db)).store_single(
                fact, user_id, session_id)
            await db.commit()
            return ok

    try:
        ok = memory_manager.run_tool(_store)
    except Exception as e:
        logger.error(f"[Tool:memory_store] 写入失败: {e}")
        return f"❌ 记忆写入失败: {e}"

    if ok:
        logger.info(f"[Tool:memory_store] 已写入 {memory_type} (user={user_id})")
        return f"✅ 已记住（{memory_type}）: {scan.sanitized[:200]}"
    return "⏭ 内容与已有记忆重复或重要性不足，未写入。"


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(memory_search_tool, __file__)
tool_registry.register(memory_store_tool, __file__)
