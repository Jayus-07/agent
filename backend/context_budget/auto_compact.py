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

import contextvars
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
    # 语义金额标签（仅裸数字金额有）：用于「同一语义最新值优先」 supersede
    label: str | None = None


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

# 裸金额（Phase 5，P3）：语义词 + 数值，无货币符号。
# Phase 4 真实丢失案例：「预算 100,000」被摘要后丢失。
# 误判防线（§14 禁止把所有大数字当金额）：
#   - 只认固定语义词表（预算/金额/成本/…），订单号/用户ID/SKU/库存/错误码不在表内；
#   - 词与数字之间的间隙不得跨越货币符号（「预算 ¥5000」交给既有货币规则）；
#   - 数字后缀是单位/日期/百分比时不当作金额（「35件」「10万」「18%」「2026-10-15」）。
_BARE_AMOUNT_LABELS = (
    "退款金额|采购价|销售额|预算|金额|成本|价格|售价|退款|收入|利润|毛利"
    "|费用|支出|单价|总价|最高|最低|上限|下限"
)
_BARE_AMOUNT = re.compile(
    rf"({_BARE_AMOUNT_LABELS})"
    rf"[^0-9%¥￥$€£]{{0,4}}?"
    rf"(\d[\d,]*(?:\.\d+)?)"
    rf"(?![\d%‰年月日万亿块/-])"
)

_extract_lock = threading.Lock()  # 正则模块级共享，抽取本身无状态，锁仅为语义清晰


def extract_protected_facts(
    rows: Iterable[tuple[int | str | None, str]],
) -> list[ProtectedFact]:
    """从待摘要消息中确定性抽取关键事实（去重保序，按配置截断）。

    裸金额「最新事实优先」supersede（Phase 5 P3）：同一语义标签的金额
    （如「预算」先 100,000 后改成 120,000）只保留最新一条，避免摘要
    把新旧两个值同时当作当前有效值。
    """
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
            for m in _BARE_AMOUNT.finditer(content):
                label, value = m.group(1), m.group(2)
                key = ("金额", value)
                if key in seen:
                    continue
                seen.add(key)
                facts.append(ProtectedFact(
                    type="金额", value=value, source_message_id=msg_id,
                    label=label))

    # 语义金额 supersede：同 label 只留最新（rows 按 message id 升序遍历）
    latest_by_label: dict[str, int] = {}
    for i, f in enumerate(facts):
        if f.label:
            latest_by_label[f.label] = i
    stale: set[int] = set()
    for label, last_idx in latest_by_label.items():
        stale.update(
            i for i, f in enumerate(facts)
            if f.label == label and i != last_idx)
    facts = [f for i, f in enumerate(facts) if i not in stale]

    return facts[:max_facts]


def format_facts_for_prompt(facts: list[ProtectedFact]) -> str:
    if not facts:
        return "（无）"
    return "\n".join(
        f"- {f.type}（{f.label}）: {f.value}" if f.label
        else f"- {f.type}: {f.value}"
        for f in facts
    )


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
    # Phase 5 分类型统计（patched 分布观测，§19）
    protected_by_type: dict = field(default_factory=dict)
    patched_by_type: dict = field(default_factory=dict)


class SyncMemorySummaryStore:
    """chat_sessions 摘要水位线的同步读写（agent_memory，连接池化）。

    走 infra/db 的共享 Engine（raw_connection 归还池），与 19 个已收口
    store 同一模式。查询层保持原生 SQL（项目决策：非 ORM）。

    并发安全（2026-09-23 STOP C）：save_summary_state 为 CAS 写——仅当
    库内水位线仍等于调用方读取时的 expected_through 才生效；否则返回
    False（说明已有更新的摘要落库，当前旧结果必须丢弃）。
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
                "SELECT summary, summary_through_message_id, summary_token_count, "
                "summary_version FROM public.chat_sessions WHERE session_id = %s",
                (self.session_id,),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return {"summary": None, "through_id": None,
                        "token_count": None, "version": None}
            return {"summary": row[0], "through_id": row[1],
                    "token_count": row[2], "version": row[3]}
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
        expected_through: int | None = None,
    ) -> bool:
        """CAS 写入水位线（2026-09-23 STOP C）。

        仅当库内 summary_through_message_id 仍等于 expected_through（调用方
        开始摘要前读到的值，NULL 视为 0）时更新并 version+1；返回 False =
        CAS 冲突（已有更新的摘要写入），调用方必须丢弃当前旧结果。
        expected_through=None 时保持旧的无条件语义（向后兼容显式覆盖入口）。
        """
        conn = self._engine().raw_connection()
        try:
            cur = conn.cursor()
            if expected_through is None:
                cur.execute(
                    "UPDATE public.chat_sessions SET summary = %s, "
                    "summary_through_message_id = %s, summary_token_count = %s, "
                    "summary_version = summary_version + 1, "
                    "summary_updated_at = NOW() WHERE session_id = %s",
                    (summary, int(through_id), int(token_count), self.session_id),
                )
            else:
                cur.execute(
                    "UPDATE public.chat_sessions SET summary = %s, "
                    "summary_through_message_id = %s, summary_token_count = %s, "
                    "summary_version = summary_version + 1, "
                    "summary_updated_at = NOW() "
                    "WHERE session_id = %s "
                    "AND COALESCE(summary_through_message_id, 0) = %s",
                    (summary, int(through_id), int(token_count),
                     self.session_id, int(expected_through)),
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

# L5 递归/重入守卫（2026-09-23 STOP C，替代原 threading.local）：
# 摘要 LLM 自身也走 proxy preflight，必须阻断再次触发 L5。
# ContextVar 配合 _invoke_llm_with_timeout 的 copy_context 传播——
# 摘要调用在 ThreadPoolExecutor 线程内执行时同样可见（threading.local
# 在 asyncio/线程池混合拓扑下既管不到 worker 线程，也会串任务）。
L5_ACTIVE: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "l5_active", default=False)


def is_l5_active() -> bool:
    return L5_ACTIVE.get()


def _acquire_l5_lock(session_id: str):
    """跨进程单飞 Redis 锁（key 含租户隔离）。

    成功返回已持有的 lock 对象（finally 释放）；None = Redis 不可用
    （降级放行：只靠进程内单飞 + 水位线 CAS 兜底，绝不阻断聊天）；
    False = 其他 worker 正在摘要（lock_conflict，本轮直接放弃）。
    """
    try:
        from backend.infra.redis.client import get_redis, is_redis_available
        if not is_redis_available():
            return None
        r = get_redis()
        if r is None:
            return None
        from backend.core.request_context import get_tool_tenant_id
        ttl = int(_cfg("CONTEXT_L5_LOCK_TTL", 60))
        key = f"context:l5:{get_tool_tenant_id() or 'default'}:{session_id}"
        lock = r.lock(key, timeout=ttl, blocking_timeout=0)
        return lock if lock.acquire(blocking=False) else False
    except Exception:
        return None  # 锁层任何异常都降级，不影响主链


def _release_l5_lock(lock) -> None:
    if lock in (None, False):
        return
    try:
        lock.release()
    except Exception:
        pass  # 锁已过期/被接管：静默（TTL 兜底，无死锁）


def _format_conversation(rows: list[tuple[int, str, str]]) -> str:
    return "\n".join(
        f"{'用户' if role == 'user' else '助手'}: {content}"
        for _mid, role, content in rows
    )


def _resolve_summary_model() -> str:
    """context_compactor 角色解析（Phase 5 正式化）。

    解析链（§七，不建第二套配置系统）：
      DB role binding（管理端 llm_model_role_bindings）
      → config fallback（CONTEXT_L5_SUMMARY_MODEL 常量/env）
      → provider adapter（角色 inherit main 兜底）
    业务代码零直连模型名；DB 改绑定随 refresh 循环准实时生效。
    解析失败不阻断（安全回退：摘要失败走确定性裁剪）。
    """
    cfg_model = str(_cfg("CONTEXT_L5_SUMMARY_MODEL", "") or "").strip()
    try:
        from backend.config import model_roles
        info = model_roles.resolve_effective("context_compactor")
        value = str(info.get("value") or "").strip()
        if info.get("source") == model_roles.SOURCE_DB and value:
            return value  # DB 显式绑定最高优先
        if cfg_model:
            return cfg_model  # config fallback 优先于 inherit main
        return value  # inherit main（或空）
    except Exception:
        logger.debug("context_compactor 角色解析失败，回落 config 常量", exc_info=True)
        return cfg_model


def _provider_supports_thinking_flag(model_name: str) -> bool:
    """该模型是否支持 enable_thinking 开关（qwen/siliconflow 系）。

    与 rag/preprocessing/llm_enrichment.py 同一口径：qwen（DashScope）
    与 siliconflow 支持顶层 enable_thinking；DeepSeek/Ollama 不识别该参数，
    保持原调用不传（传了可能报错或被网关拒绝）。
    """
    if not model_name:
        return False
    lowered = model_name.lower()
    if "qwen" in lowered or "qwq" in lowered:
        return True
    try:
        from backend.infra.llm.proxy import _get_provider_for
        return _get_provider_for(model_name) in ("qwen", "siliconflow")
    except Exception:
        return False


def _summary_invoke_kwargs(model_name: str) -> dict:
    """摘要调用的确定性收口参数（Phase 5 P1）：短输出 + 低温 + 显式关 thinking。

    Phase 4/5 实测根因：qwen 系混合思考模型在通用 openai driver 下不传
    enable_thinking 时思考链默认开启，摘要一次 2.7k thinking tokens / 25~41s。
    摘要任务不需要 reasoning：只要短、准、结构化、稳定。
    """
    kwargs: dict = {
        "max_tokens": int(_cfg("CONTEXT_L5_SUMMARY_MAX_TOKENS", 512)),
        "temperature": float(_cfg("CONTEXT_L5_SUMMARY_TEMPERATURE", 0.2)),
    }
    if _provider_supports_thinking_flag(model_name):
        kwargs["extra_body"] = {"enable_thinking": False}
    return kwargs


def _invoke_llm_with_timeout(prompt: str):
    """摘要 LLM 调用（带超时；超时按失败处理，不阻塞调用方）。

    模型经 context_compactor 角色解析（_resolve_summary_model），在 executor
    线程内 set_request_model 绑定，保留 proxy 的限流/韧性/<think> 剥离/token
    统计链路；调用参数统一收口 _summary_invoke_kwargs（关 thinking + 限输出）。

    ContextVar 传播（STOP C）：ThreadPoolExecutor.submit 不携带 contextvars，
    显式 copy_context().run 让 session/tenant/L5_ACTIVE 守卫在 worker 线程
    内同样可见（proxy preflight 据此正确跳过递归触发）。

    Fallback 原则（Phase 5 §九）：摘要模型调用失败**不换模型重试**、不自动
    降级到主 reasoning 模型 —— L5 是保险层，直接失败回退确定性裁剪。
    """
    from backend.infra.llm import llm

    model_name = _resolve_summary_model()
    invoke_kwargs = _summary_invoke_kwargs(model_name)

    def _call():
        if model_name:
            from backend.infra.llm.proxy import set_request_model
            set_request_model(model_name)
        return llm.invoke(prompt, **invoke_kwargs)

    timeout = float(_cfg("CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS", 30))
    ctx = contextvars.copy_context()
    future = _llm_executor.submit(ctx.run, _call)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeout:
        future.cancel()
        raise TimeoutError(f"L5 摘要 LLM 调用超时（{timeout}s）")


def run_incremental_summary(
    session_id: str, store: SyncMemorySummaryStore,
) -> SummaryOutcome | None:
    """增量摘要一次：旧 summary + 水位线之后的新消息 → 新 summary。

    并发安全（2026-09-23 STOP C）：
      - 进程内：L5_ACTIVE ContextVar 递归守卫（调用方 manager 亦有单飞）
      - 跨进程：Redis 单飞锁（TTL > 摘要超时；Redis 不可用降级放行）
      - 落库：水位线 CAS——expected_through 不匹配即丢弃结果（stale_waterline）

    返回 None 表示本轮不摘要（无可增量内容 / delta 太小 / LLM 失败 /
    锁冲突 / CAS 冲突），旧摘要与旧水位线原样保留（幂等可重试）。
    调用方安全回退，绝不阻断主聊天链。
    """
    if L5_ACTIVE.get():
        return None  # 摘要 LLM 自身的 preflight 重入，禁止递归
    started = time.perf_counter()
    lock = _acquire_l5_lock(session_id)
    if lock is False:
        # 其他 worker 正在摘要同一 session：本轮直接用裁剪结果
        try:
            from backend.context_budget.metrics import record_l5_attempt
            record_l5_attempt(status="failed", reason="lock_conflict")
        except Exception:
            pass
        logger.info(
            f"[AutoCompact:{session_id}] L5 单飞锁被占用，跳过本轮摘要")
        return None
    token = L5_ACTIVE.set(True)
    try:
        return _run_incremental_summary_locked(session_id, store, started)
    finally:
        L5_ACTIVE.reset(token)
        _release_l5_lock(lock)


def _run_incremental_summary_locked(
    session_id: str, store: SyncMemorySummaryStore, started: float,
) -> SummaryOutcome | None:
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
        if new_through <= through:
            return None  # 防御：水位线不允许回退
        if not store.save_summary_state(
                summary, new_through, token_count,
                expected_through=through):
            # CAS 冲突：已有更新的摘要落库，当前旧结果必须丢弃
            try:
                from backend.context_budget.metrics import record_l5_attempt
                record_l5_attempt(status="failed", reason="stale_waterline")
            except Exception:
                pass
            logger.info(
                f"[AutoCompact:{session_id}] 水位线 CAS 冲突"
                f"（expected_through={through}），丢弃本轮旧摘要")
            return None

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
            protected_by_type=_count_by_type(facts),
            patched_by_type=_count_by_type(missing),
        )
        _record_l5_metrics(outcome)
        logger.info(
            f"context_compacted level=L5 action=auto_compact "
            f"delta_messages={outcome.delta_message_count} "
            f"through_id={new_through} summary_tokens={token_count} "
            f"protected_facts={outcome.protected_fact_count} "
            f"patched={outcome.patched_fact_count} "
            f"llm_tokens={outcome.llm_prompt_tokens}+{outcome.llm_completion_tokens} "
            f"latency_ms={outcome.latency_ms}")
        return outcome
    except TimeoutError as e:
        _record_l5_failure("timeout")
        logger.warning(
            f"[AutoCompact:{session_id}] 增量摘要超时，保留旧摘要与旧水位线"
            f"（安全回退）: {e}")
        return None
    except ValueError as e:
        # 空摘要 / 水位线落库失败等确定性业务失败
        msg = str(e)
        reason = "empty_summary" if "空" in msg else "db_error"
        _record_l5_failure(reason)
        logger.warning(
            f"[AutoCompact:{session_id}] 增量摘要失败，保留旧摘要与旧水位线"
            f"（安全回退）: {e}")
        return None
    except Exception as e:
        _record_l5_failure("provider_error")
        logger.warning(
            f"[AutoCompact:{session_id}] 增量摘要失败，保留旧摘要与旧水位线"
            f"（安全回退）: {e}")
        return None


def _count_by_type(facts: list[ProtectedFact]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in facts:
        counts[f.type] = counts.get(f.type, 0) + 1
    return counts


def _record_l5_metrics(outcome: SummaryOutcome) -> None:
    """成功路径指标：l5_total(success) + ProtectedFacts 分层（§26 低基数）。

    extracted = 抽取总量；patched = LLM 遗漏由确定性补丁补回；
    preserved = LLM 原生保留（extracted - patched，按类型对齐）。
    """
    try:
        from backend.context_budget.metrics import (
            record_l5_attempt,
            record_protected_fact,
        )
        record_l5_attempt(status="success", reason="success")
        for fact_type, total in outcome.protected_by_type.items():
            record_protected_fact(fact_type=fact_type, result="extracted")
            patched = outcome.patched_by_type.get(fact_type, 0)
            for _ in range(max(0, total - patched)):
                record_protected_fact(fact_type=fact_type, result="preserved")
        for fact_type, patched in outcome.patched_by_type.items():
            for _ in range(patched):
                record_protected_fact(fact_type=fact_type, result="patched")
    except Exception:
        logger.debug("L5 metric 记录失败", exc_info=True)


def _record_l5_failure(reason: str) -> None:
    try:
        from backend.context_budget.metrics import record_l5_attempt
        record_l5_attempt(status="failed", reason=reason)
    except Exception:
        logger.debug("L5 metric 记录失败", exc_info=True)


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
    """旧 L2 摘要 / L4 折叠投影 / 新 <historical_context> 块：可被新摘要整体替换。

    兼容三种历史形态：
      - 旧 SystemMessage 会话摘要（L2_SUMMARY_MARKER 开头）
      - 旧 SystemMessage 折叠投影（L4_FOLD_MARKER）
      - 新形态：固定 policy SystemMessage + <historical_context> AIMessage
        （role_safety 自产，2026-09-23 P0-2 起）
    """
    from backend.context_budget.role_safety import (
        is_historical_data_message,
        is_policy_system_message,
    )

    content = getattr(msg, "content", "")
    if isinstance(content, str) and (
        content.startswith(L2_SUMMARY_MARKER) or L4_FOLD_MARKER in content[:80]
    ):
        return True
    return is_policy_system_message(msg) or is_historical_data_message(msg)


def fold_rebuild(
    messages: list,
    summary_text: str,
    *,
    keep_recent_turns: int | None = None,
) -> tuple[list, int, int | None]:
    """把较旧历史替换为「固定 policy SystemMessage + <historical_context>
    摘要 AIMessage」（重建 active projection）。

    角色安全（P0-2）：摘要由用户历史生成，属 untrusted data，只进
    AIMessage 数据块；SystemMessage 仅承载进程内固定 policy 文本。

    保留：SystemMessage（旧摘要/折叠投影除外，它们被新摘要取代）、
    最近 keep_recent_turns 轮、当前消息。返回 (新消息列表, 被替换条数,
    边界下标|None)。无可保留轮时原样返回。
    """
    if not messages:
        return messages, 0, None
    if keep_recent_turns is None:
        keep_recent_turns = int(_cfg("CONTEXT_L4_KEEP_RECENT_TURNS", 4))

    from backend.context_budget.role_safety import build_historical_context

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

    new_messages = (
        kept_head
        + build_historical_context(summary_text)
        + list(messages[boundary:])
    )
    return new_messages, replaced, boundary


async def run_auto_compact_async(session_id: str) -> SummaryOutcome | None:
    """异步入口（事件循环上下文的 fire-and-forget / 显式调用）。

    核心是同步 DB + 同步 LLM，统一丢线程池，不阻塞事件循环。
    """
    import asyncio
    return await asyncio.to_thread(
        run_incremental_summary, session_id, SyncMemorySummaryStore(session_id))
