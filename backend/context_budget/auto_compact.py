"""context_budget.auto_compact — L5 AutoCompact（LLM 增量摘要，最后一道防线）

Phase 3（2026-09-22）设计要点：

- **最后一道防线**：只在 L1/L2/L3/L4 全部执行后 usage_ratio 仍达
  CONTEXT_L5_TRIGGER_RATIO（默认 0.90）才触发；正常请求永不走本模块。
- **红线**：只推进 active prompt projection 的摘要水位
  （chat_sessions.summary_through_message_id），**绝不删除/改写
  chat_messages 原始行**；前端历史记录完整可查。
- **增量摘要**：旧 summary + (through_id, boundary_id) 区间的新消息 →
  新 summary，不重复总结整段会话。boundary 之前的最近
  CONTEXT_L4_KEEP_RECENT_TURNS 轮保持原文（与 L4 语义一致，复用配置）。
- **关键实体保护**：摘要前后各一道确定性防线——
  ①调用 LLM 前正则抽取 ProtectedFacts 注入 prompt；
  ②摘要返回后校验 facts 是否原样保留，遗漏的**追加**到 [关键实体]
  小节（零额外 API 调用，不重调 LLM）。
- **安全回退**：摘要失败/超时/无收益 → 返回 None，保留旧摘要与旧
  水位线（幂等可重试），调用方继续用裁剪后的上下文，绝不阻断请求。
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import BaseModel

from backend.shared.logger import logger


def _cfg(name: str, default: int | float | bool) -> Any:
    """业务代码禁止直接 os.getenv，统一走 config（env 覆盖即时生效）。"""
    import backend.config as config
    return getattr(config, name, default)


# ---------------------------------------------------------------------------
# ProtectedFacts：确定性关键实体抽取（第一版规则，不依赖 LLM 自觉）
# ---------------------------------------------------------------------------

class ProtectedFact(BaseModel):
    type: str
    value: str
    source_message_id: int | str | None = None


# (类型, 模式) —— 全部用捕获组取值；顺序即优先级
_ENTITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("订单号", re.compile(
        r"(?:订单号|订单编号)(?:是|为)?\s*[:：]?\s*"
        r"([A-Za-z0-9][A-Za-z0-9\-_]{3,})", re.IGNORECASE)),
    ("订单号", re.compile(r"\bORD[A-Za-z0-9\-_]*\b")),
    ("SKU", re.compile(
        r"\bSKU\s*[:：\-]?\s*([A-Za-z0-9][A-Za-z0-9\-_]{2,})", re.IGNORECASE)),
    ("业务ID", re.compile(
        r"(?:商品|用户|项目|客户|供应商)\s*(?:ID|id)\s*[:：]?\s*([A-Za-z0-9\-_]+)")),
    ("金额", re.compile(r"[¥￥$]\s?\d[\d,]*(?:\.\d+)?")),
    ("金额", re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:元|万|万美元|人民币|美元)")),
    ("百分比", re.compile(r"\d+(?:\.\d+)?\s*%")),
    ("日期", re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?")),
    ("时间", re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")),
    ("URL", re.compile(r"https?://\S+")),
    ("版本号", re.compile(r"\bv?\d+(?:\.\d+){2,}\b")),
    ("错误码", re.compile(r"\b[A-Z]{2,6}-\d{2,}\b")),
    ("数量", re.compile(r"\d+\s*(?:个|件|台|条|名|人|箱|包|袋|kg|KG|克|千克|升|毫升)")),
]

_extract_lock = threading.Lock()  # 正则模块级共享，抽取本身无状态，锁仅为语义清晰


def extract_protected_facts(
    rows: Iterable[tuple[int | str | None, str]],
) -> list[ProtectedFact]:
    """从待摘要消息中确定性抽取关键事实（去重保序，按配置截断）。"""
    seen: set[tuple[str, str]] = set()
    facts: list[ProtectedFact] = []
    max_facts = int(_cfg("CONTEXT_L5_MAX_PROTECTED_FACTS", 40))
    with _extract_lock:
        for row in rows:
            msg_id, content = row[0], row[-1]
            if not content:
                continue
            for fact_type, pattern in _ENTITY_PATTERNS:
                for m in pattern.finditer(content):
                    value = (m.group(1) if pattern.groups else m.group(0)).strip()
                    if not value:
                        continue
                    key = (fact_type, value)
                    if key in seen:
                        continue
                    seen.add(key)
                    facts.append(ProtectedFact(
                        type=fact_type, value=value, source_message_id=msg_id))
                    if len(facts) >= max_facts:
                        return facts
    return facts


def format_facts_for_prompt(facts: list[ProtectedFact]) -> str:
    if not facts:
        return "（无）"
    return "\n".join(f"- {f.type}: {f.value}" for f in facts)


def validate_and_patch(
    summary: str, facts: list[ProtectedFact],
) -> tuple[str, list[ProtectedFact]]:
    """摘要后确定性校验：遗漏的 ProtectedFacts **追加**进 [关键实体] 小节。

    返回 (补丁后摘要, 遗漏清单)。追加而非重调 LLM：零额外 API 成本。
    """
    if not facts:
        return summary, []
    lowered = summary.lower()
    missing = [f for f in facts if f.value.lower() not in lowered]
    if not missing:
        return summary, []
    patch = "\n\n[关键实体]\n" + "\n".join(
        f"- {f.type}: {f.value}" for f in missing)
    return summary + patch, missing


# ---------------------------------------------------------------------------
# 摘要水位线 Store（同步协议；异步调用方经 to_thread 复用同一实现）
# ---------------------------------------------------------------------------

@dataclass
class SummaryOutcome:
    summary: str
    through_id: int
    token_count: int
    delta_message_count: int
    protected_fact_count: int
    patched_fact_count: int
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    latency_ms: int = 0


class SyncMemorySummaryStore:
    """chat_sessions 摘要水位线的同步读写（agent_memory，连接池化）。

    走 infra/db 的共享 Engine（raw_connection 归还池），与 19 个已收口
    store 同一模式。查询层保持原生 SQL（项目决策：非 ORM）。
    """

    def __init__(self, session_id: str):
        self.session_id = session_id

    @staticmethod
    def _engine():
        from backend.infra.db import get_memory_engine
        return get_memory_engine()

    def get_summary_state(self) -> dict:
        conn = self._engine().raw_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT summary, summary_through_message_id, summary_token_count "
                "FROM public.chat_sessions WHERE session_id = %s",
                (self.session_id,),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return {"summary": None, "through_id": None, "token_count": None}
            return {"summary": row[0], "through_id": row[1], "token_count": row[2]}
        finally:
            conn.close()  # 归还连接池

    def summarizable_before_id(self, keep_recent_turns: int) -> int | None:
        """最近 keep_recent_turns 轮（由 user 消息开轮）之前的边界消息 id。

        返回 None = 会话轮数不足，本轮无可增量摘要的范围。
        """
        conn = self._engine().raw_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM public.chat_messages "
                "WHERE session_id = %s AND role = 'user' "
                "ORDER BY id DESC OFFSET %s LIMIT 1",
                (self.session_id, max(0, int(keep_recent_turns) - 1)),
            )
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
        finally:
            conn.close()

    def load_delta_messages(
        self, after_id: int | None, before_id: int, limit: int,
    ) -> list[tuple[int, str, str]]:
        conn = self._engine().raw_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, role, content FROM public.chat_messages "
                "WHERE session_id = %s AND id > %s AND id < %s "
                "ORDER BY id ASC LIMIT %s",
                (self.session_id, int(after_id or 0), int(before_id), int(limit)),
            )
            rows = cur.fetchall()
            conn.commit()
            return [(r[0], r[1], r[2] or "") for r in rows]
        finally:
            conn.close()

    def save_summary_state(
        self, summary: str, through_id: int, token_count: int,
    ) -> bool:
        conn = self._engine().raw_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE public.chat_sessions SET summary = %s, "
                "summary_through_message_id = %s, summary_token_count = %s, "
                "summary_updated_at = NOW() WHERE session_id = %s",
                (summary, int(through_id), int(token_count), self.session_id),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 增量摘要核心
# ---------------------------------------------------------------------------

_llm_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="l5-compact")


def _format_conversation(rows: list[tuple[int, str, str]]) -> str:
    return "\n".join(
        f"{'用户' if role == 'user' else '助手'}: {content}"
        for _mid, role, content in rows
    )


def _invoke_llm_with_timeout(prompt: str):
    """摘要 LLM 调用（带超时；超时按失败处理，不阻塞调用方）。

    CONTEXT_L5_SUMMARY_MODEL 非空时按模型名覆盖（在 executor 线程内绑定，
    保留 proxy 的限流/韧性/<think> 剥离/token 统计链路）。Phase 4 实测：
    默认 LLM 解析指向未配置密钥的模型时摘要必然失败回退（ChatAnthropic
    validation error），此配置给治理层一个明确的模型指定出口。
    """
    from backend.infra.llm import llm

    model_name = str(_cfg("CONTEXT_L5_SUMMARY_MODEL", "") or "").strip()

    def _call():
        if model_name:
            from backend.infra.llm.proxy import set_request_model
            set_request_model(model_name)
        return llm.invoke(prompt)

    timeout = float(_cfg("CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS", 20))
    future = _llm_executor.submit(_call)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeout:
        future.cancel()
        raise TimeoutError(f"L5 摘要 LLM 调用超时（{timeout}s）")


def run_incremental_summary(
    session_id: str, store: SyncMemorySummaryStore,
) -> SummaryOutcome | None:
    """增量摘要一次：旧 summary + 水位线之后的新消息 → 新 summary。

    返回 None 表示本轮不摘要（无可增量内容 / delta 太小 / LLM 失败），
    旧摘要与旧水位线原样保留（幂等可重试）。调用方安全回退。
    """
    started = time.perf_counter()
    try:
        state = store.get_summary_state()
        old_summary = state.get("summary") or ""
        through = int(state.get("through_id") or 0)

        keep_turns = int(_cfg("CONTEXT_L4_KEEP_RECENT_TURNS", 4))
        boundary_id = store.summarizable_before_id(keep_turns)
        if not boundary_id or boundary_id <= through:
            return None  # 无可增量摘要的新内容（含最近 N 轮保护）

        rows = store.load_delta_messages(
            through, boundary_id,
            int(_cfg("CONTEXT_L5_MAX_DELTA_MESSAGES", 200)),
        )
        if len(rows) < int(_cfg("CONTEXT_L5_MIN_DELTA_MESSAGES", 2)):
            return None  # delta 太小，不值得一次 LLM 调用

        facts = extract_protected_facts(rows)
        rendered = _render_summary_prompt(old_summary, rows, facts)
        resp = _invoke_llm_with_timeout(rendered)
        summary = getattr(resp, "content", None) or str(resp)
        summary = summary.strip()
        if not summary:
            raise ValueError("摘要 LLM 返回空内容")

        summary, missing = validate_and_patch(summary, facts)

        token_count = _count_tokens(summary)
        new_through = max(int(r[0]) for r in rows)
        if not store.save_summary_state(summary, new_through, token_count):
            raise ValueError("摘要水位线落库失败（会话不存在？）")

        usage = getattr(resp, "response_metadata", None) or {}
        tu = usage.get("token_usage", {}) or {}
        outcome = SummaryOutcome(
            summary=summary, through_id=new_through, token_count=token_count,
            delta_message_count=len(rows),
            protected_fact_count=len(facts),
            patched_fact_count=len(missing),
            llm_prompt_tokens=int(tu.get("prompt_tokens") or 0),
            llm_completion_tokens=int(tu.get("completion_tokens") or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        logger.info(
            f"context_compacted level=L5 action=auto_compact "
            f"delta_messages={outcome.delta_message_count} "
            f"through_id={new_through} summary_tokens={token_count} "
            f"protected_facts={outcome.protected_fact_count} "
            f"patched={outcome.patched_fact_count} "
            f"llm_tokens={outcome.llm_prompt_tokens}+{outcome.llm_completion_tokens} "
            f"latency_ms={outcome.latency_ms}")
        return outcome
    except Exception as e:
        logger.warning(
            f"[AutoCompact:{session_id}] 增量摘要失败，保留旧摘要与旧水位线"
            f"（安全回退）: {e}")
        return None


def _render_summary_prompt(
    old_summary: str, rows: list[tuple[int, str, str]],
    facts: list[ProtectedFact],
) -> str:
    from backend.prompts.service import prompt_service
    r = prompt_service.render_sync(
        "memory.session.auto_compact",
        existing_summary=old_summary or "（无，这是首次摘要）",
        new_conversation=_format_conversation(rows),
        protected_facts=format_facts_for_prompt(facts),
    )
    return r.text


def _count_tokens(text: str) -> int:
    from backend.memory.token_budget import count_tokens
    return count_tokens(text)


# ---------------------------------------------------------------------------
# active prompt projection 重建（只动本轮发送内容，不碰任何持久化数据）
# ---------------------------------------------------------------------------

# 与 MemoryService.start_session 的 L2 摘要注入文案保持同一标记前缀，
# 后续重建时才能识别并整体替换（不叠两层摘要）
L2_SUMMARY_MARKER = "以下是本会话早期对话的摘要"
L4_FOLD_MARKER = "[Earlier conversation folded]"


def _is_system(msg: Any) -> bool:
    return type(msg).__name__ == "SystemMessage"


def _is_replaceable_summary(msg: Any) -> bool:
    """旧 L2 摘要 / L4 折叠投影：可被新摘要整体替换的消息。"""
    content = getattr(msg, "content", "")
    return isinstance(content, str) and (
        content.startswith(L2_SUMMARY_MARKER) or L4_FOLD_MARKER in content[:80]
    )


def fold_rebuild(
    messages: list,
    summary_text: str,
    *,
    keep_recent_turns: int | None = None,
) -> tuple[list, int, int | None]:
    """把较旧历史替换为一条会话摘要 SystemMessage（重建 active projection）。

    保留：SystemMessage（旧摘要/折叠投影除外，它们被新摘要取代）、
    最近 keep_recent_turns 轮、当前消息。返回 (新消息列表, 被替换条数,
    边界下标|None)。无可保留轮时原样返回。
    """
    from langchain_core.messages import SystemMessage

    if not messages:
        return messages, 0, None
    if keep_recent_turns is None:
        keep_recent_turns = int(_cfg("CONTEXT_L4_KEEP_RECENT_TURNS", 4))

    non_system_idx = [i for i, m in enumerate(messages) if not _is_system(m)]
    user_positions = [
        i for i in non_system_idx
        if type(messages[i]).__name__ == "HumanMessage"
    ]
    if len(user_positions) <= keep_recent_turns:
        return messages, 0, None  # 轮数不足，无从重建

    boundary = user_positions[-keep_recent_turns]
    head = messages[:boundary]
    kept_head = [m for m in head if _is_system(m) and not _is_replaceable_summary(m)]
    replaced = len(head) - len(kept_head)
    if replaced <= 0 and not any(_is_replaceable_summary(m) for m in head):
        return messages, 0, None  # 摘要无可替换内容，不白做

    summary_msg = SystemMessage(
        content=f"{L2_SUMMARY_MARKER}，可结合它理解用户当前问题：\n{summary_text}")
    new_messages = kept_head + [summary_msg] + list(messages[boundary:])
    return new_messages, replaced, boundary


async def run_auto_compact_async(session_id: str) -> SummaryOutcome | None:
    """异步入口（事件循环上下文的 fire-and-forget / 显式调用）。

    核心是同步 DB + 同步 LLM，统一丢线程池，不阻塞事件循环。
    """
    import asyncio
    return await asyncio.to_thread(
        run_incremental_summary, session_id, SyncMemorySummaryStore(session_id))
