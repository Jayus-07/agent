"""Pipeline Trace 收集 — 线程安全，结构化接口，前端无需字符串解析

统一 API（2026-07-16）：
  - 仅 start_span / end_span / add_event 三个方法
  - parent_id=None 表示 root span；省略则自动取当前 root_span_id
  - type 未指定时按 span_id 自动推断（llm_generate→llm_call, rerank→rerank, ...）
"""
from __future__ import annotations

import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import List

from backend.shared.logger import logger

MAX_TRACES = 200


def _now_iso() -> str:
    """UTC ISO-8601 毫秒时间戳。

    旧实现秒级精度，同一秒内多个 span 无法排序（瀑布图只能靠 sequence 兜底）；
    升级到毫秒后时间线可精确重建。
    """
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"

# 异步安全：用 contextvars 隔离并发请求的 current_trace
# threading.local 在 asyncio 单线程 event loop 下无法跨 await 隔离
_current_trace_var: ContextVar[TraceRecord | None] = ContextVar(
    "trace_current", default=None
)

# 子链嵌入父 trace 时的"作用域根" span id。
# 场景：RAGChain 被 agent 调用时，不创建独立子 trace，
# 而是将 RAG spans 嵌入 agent trace 的 "rag_skill" span 下。
# set_scope_root("rag_skill") 后，所有未指定 parent_id 的 start_span
# 自动以 "rag_skill" 为父，而非 trace.root_span_id。
_scope_root_var: ContextVar[str | None] = ContextVar(
    "trace_scope_root", default=None
)

# span_id → type 自动推断表（type 未传时使用）
_TYPE_INFER: dict[str, str] = {
    "llm_generate": "llm_call",
    "query_rewrite": "llm_call",
    "hybrid_retrieval": "retrieval",
    "retrieval": "retrieval",
    "chunk_retrieval": "retrieval",
    "enhanced_hybrid_retrieval": "retrieval",
    "rerank": "rerank",
    "mq_check": "tool_call",
    "citation": "tool_call",
    "faithfulness": "tool_call",
    # Evidence Gate (P0) — 对齐方案 §0 / §3
    "evidence_gate_retrieval": "retrieval_gate",
    "evidence_gate_rerank": "rerank_gate",
    "evidence_gate_evaluation": "faithfulness_gate",
    "self_correction_rewrite": "llm_call",
}


class WorkflowKind(str, Enum):
    """Trace 用途分类 — 前端按 kind 路由渲染（RAG_QUERY 蓝色 / KNOWLEDGE_INDEX 绿色）。"""
    RAG_QUERY = "rag_query"                # chain.py: RAG 检索问答
    KNOWLEDGE_INDEX = "knowledge_index"    # indexer.py: 文档索引流水线
    LG_WORKFLOW = "langgraph_workflow"     # Phase 3: LangGraph astream_events 自动埋点
    OTHER = "other"


class SpanKind(str, Enum):
    """Span 节点类型枚举 — 强约束。type 字符串字段保留兼容历史数据。"""
    # 通用
    LLM = "llm"
    AGENT = "agent"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    RERANK = "rerank"
    CITATION = "citation"
    FAITHFULNESS = "faithfulness"

    # Evidence Gate（对齐方案 §0 / §3；type 字符串小写与 _TYPE_INFER 对齐）
    RETRIEVAL_GATE = "retrieval_gate"
    RERANK_GATE = "rerank_gate"
    FAITHFULNESS_GATE = "faithfulness_gate"
    SELF_CORRECTION = "self_correction"

    # Knowledge Index（Phase 1）
    INDEX_UPLOAD = "index_upload"
    INDEX_LOAD = "index_load"
    INDEX_PARSE = "index_parse"
    INDEX_CLEAN = "index_clean"
    INDEX_DEDUP = "index_dedup"
    INDEX_CHUNK = "index_chunk"
    INDEX_EMBED = "index_embed"
    INDEX_VECTOR_DB = "index_vector_db"
    INDEX_METADATA = "index_metadata"

    # P0-1: Metadata 子 Span 拆分 (7 个子阶段)
    INDEX_CLASSIFY = "index_classify"          # 分类 (classify_with_confidence)
    INDEX_QUALITY_CHECK = "index_quality"     # 质量检查 (assess_quality)
    INDEX_DEDUP_MINHASH = "index_dedup_minhash"  # MinHash 去重
    INDEX_KEYWORD_RULE = "index_keyword_rule"  # 规则关键词
    INDEX_LLM_DECIDE = "index_llm_decide"      # LLM 决策 (是否调 LLM)
    INDEX_LLM_GENERATE = "index_llm_generate"  # LLM 生成 (keywords/summary/entities)
    INDEX_SECTION = "index_section"            # 章节提取
    INDEX_DOMAIN_CLASSIFY = "index_domain_classify"  # 业务域分类

    # 工作流
    WORKFLOW = "workflow"
    ROUTER = "router"
    KB_ROUTING = "kb_routing"

    # Customer Service
    CS_ROUTING = "cs_routing"
    CS_KNOWLEDGE = "cs_knowledge"
    CS_BUSINESS_QUERY = "cs_business_query"
    CS_BUSINESS_ACTION = "cs_business_action"
    CS_COMPLAINT = "cs_complaint"
    CS_HANDOFF = "cs_handoff"
    CS_CONFIRMATION = "cs_confirmation"
    CS_GUARD = "cs_guard"
    CS_SUPERVISOR = "cs_supervisor"
    CS_EXPERT = "cs_expert"
    CS_REPORTER = "cs_reporter"
    CS_STATE_TRANSITION = "cs_state_transition"
    CS_STATE_LOADER = "cs_state_loader"


class SpanName:
    """RAG 链路统一 span 显示名（P1 整改：同一阶段唯一标准名称）。

    前端 UI、trace 存储、评测模块共用这一套映射；
    代码中不允许同一阶段同时出现中英文两个名称。
    """
    RETRIEVAL = "检索"            # 向量+BM25 混合检索（hybrid/chunk/retrieval 层）
    CHUNK_RETRIEVAL = "Chunk 检索"  # ChunkLevelRetriever 层
    ENHANCED_RETRIEVAL = "增强混合检索"  # EnhancedHybridRetrieval 三路召回层
    HYBRID_RETRIEVAL = "混合检索"   # 原始 Hybrid 兜底检索层
    MULTI_QUERY = "多查询扩展"      # MultiQuery 复杂度检测与 LLM 改写
    META_PARSE = "META 解析"       # LLM 输出 <!--META--> 注释解析
    CITATION = "引文校验"          # Citation 支持度校验
    CLAIM_VERIFY = "事实校验"      # 程序化 Claim（数字/时效）原文比对
    EVALUATE = "忠实度评估"        # Faithfulness 忠实性评估


@dataclass
class Span:
    """通用 Span — 树形结构。

    type 用字符串保留向后兼容；新增 SpanKind 枚举作为强约束。
    """
    span_id: str
    parent_id: str | None          # None = root span
    name: str                      # 人类可读名称
    type: str                      # llm_call | retrieval | rerank | agent | tool_call | workflow | ...
    kind: str = SpanKind.TOOL.value  # SpanKind 枚举值（强约束）
    status: str = "success"        # success | error | skipped
    start_time: str = ""
    end_time: str = ""
    duration_ms: int = 0
    sequence: int = 0              # 同 parent 下的排序序号
    retry_count: int = 0           # 重试次数
    metrics: dict = field(default_factory=dict)
    input: dict | None = None
    output: dict | None = None
    events: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class TraceRecord:
    id: str
    request_id: str = ""
    timestamp: str = ""
    session_id: str = ""
    model: str = ""
    provider: str = ""
    question: str = ""
    answer_preview: str = ""
    answer_len: int = 0
    duration_ms: int = 0
    total_ms: int = 0
    usage: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)
    cost_usd: float = 0.0  # 全 trace 成本（llm_usage 明细回填；含 embedding/rerank）
    error: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    spans: List[Span] = field(default_factory=list)
    root_span_id: str = ""
    workflow_name: str = ""
    workflow_kind: str = WorkflowKind.OTHER.value  # WorkflowKind 枚举值
    sla_threshold_ms: int = 10000
    parent_id: str | None = None
    children_ids: List[str] = field(default_factory=list)
    graph: dict | None = None
    tags: dict = field(default_factory=dict)  # 自由 tag（kb_id, user_id, doc_id 等）


class TraceCollector:
    """线程/异步安全的 Tracing 收集器 — 统一 Span API。

    双轨 _current_trace：
      - 实例字段 _thread_current：用于 sync threadpool（如 FastAPI sync handler），
        ThreadPoolExecutor worker 内多线程共享同一 trace
      - contextvar _current_trace_var：用于 asyncio，每个 task 独立持有自己的 trace
    start_span 优先读 contextvar（async 更安全），fallback 到实例字段（threadpool 兼容）。
    """

    def __init__(self, max_size: int = MAX_TRACES):
        self._lock = threading.Lock()
        self._thread_current: TraceRecord | None = None  # sync path
        self._span_seq: int = 0
        self._listeners: list = []  # span lifecycle subscribers (Phase 1.5)
        # 嵌套 trace 恢复栈：子 trace id → 父 trace。
        # 场景：agent 图内嵌 RAGChain.ask() 会开自己的 trace，
        # finish() 后必须把外层 agent trace 恢复为 current，
        # 否则后续 span 全部落入 noop（浏览器实测发现）。
        self._parents: dict[str, TraceRecord] = {}

    # =====================================================
    # 统一 API
    # =====================================================

    def start(self, question: str = "", session_id: str = "default",
              workflow_name: str = "rag_agent",
              workflow_kind: str = WorkflowKind.OTHER.value) -> TraceRecord:
        """开始一次新的 trace。线程/异步安全。

        Args:
            question: 用户问题或任务标识（Knowledge Index 时可传文件名）
            session_id: 会话 ID（Knowledge Index 时可为空）
            workflow_name: 工作流名称
            workflow_kind: WorkflowKind 枚举值（前端按 kind 路由渲染）
        """
        rid = uuid.uuid4().hex[:12]
        # 嵌套检测：已有 active trace 时建立父子链（而非覆盖后丢失外层上下文）。
        # 典型场景：MultiAgent 图内执行 rag_skill → RAGChain.ask() 自带 trace。
        prev = _current_trace_var.get() or self._thread_current
        trace = TraceRecord(
            id=rid,
            request_id=rid,
            timestamp=_now_iso(),
            session_id=session_id,
            question=question,
            workflow_name=workflow_name,
            workflow_kind=workflow_kind,
            parent_id=prev.id if prev else None,
        )
        # 反馈/评测闭环需要服务端可验证的 Trace 归属；从权威请求上下文
        # 注入租户与操作者标签，绝不消费请求体中客户端自报的身份字段。
        try:
            from backend.core.request_context import (
                get_tool_tenant_id,
                get_tool_user_id,
            )
            if get_tool_tenant_id():
                trace.tags["tenant_id"] = get_tool_tenant_id()
            if get_tool_user_id():
                trace.tags["user_id"] = get_tool_user_id()
        except Exception:
            logger.debug("[Tracer] Trace 归属标签注入失败", exc_info=True)
        if prev is not None:
            prev.children_ids.append(rid)
            # P1-6: span → 子 trace 关联 — 在触发方 span 上记录子 trace id，
            # 前端瀑布图可从父 span 直接跳转查看子 trace（软失败不影响主流程）。
            if prev.spans:
                try:
                    ids = prev.spans[-1].metrics.setdefault("child_trace_ids", [])
                    if rid not in ids:
                        ids.append(rid)
                except Exception:
                    logger.debug("[Tracer] child_trace_ids 写入失败", exc_info=True)
        with self._lock:
            self._span_seq = 0
            self._thread_current = trace
            if prev is not None:
                self._parents[rid] = prev
        _current_trace_var.set(trace)
        # O2: 自动注入 trace_id/session_id 到日志
        from backend.shared.logger import set_log_context
        set_log_context(trace_id=rid, session_id=session_id)
        return trace

    def bind(self, trace: TraceRecord | None) -> None:
        """显式把 trace 绑定到当前执行上下文（ContextVar）。

        场景：P1 流式改造后 graph.stream 在独立 worker 线程执行，
        新线程不继承 producer 线程的 ContextVar，须在 worker 内重新绑定，
        否则该线程内的 start_span 全部落 noop（软失败，静默丢 trace）。
        """
        _current_trace_var.set(trace)

    def start_span(self, span_id: str, parent_id: str | None = None,
                   name: str = "", type: str = "",
                   kind: str = SpanKind.TOOL.value,
                   input: dict = None) -> Span:
        """创建 Span 并开始计时。

        参数：
          span_id:    唯一标识（同 trace 内不重复）
          parent_id:  None=root span, 省略=自动取当前 trace 的 root_span_id
          name:       人类可读名称（省略用 span_id）
          type:       llm_call|retrieval|rerank|agent|tool_call|...（省略按 span_id 推断）
          kind:       SpanKind 枚举值（强约束，默认 TOOL）
          input:      输入快照（可选）

        Returns:
            Span 对象。如果当前没有 active trace（contextvar + thread field 都为空），
            返回一个 noop Span（不抛 RuntimeError），让业务代码继续运行。
            这样 LangGraph 在不同 thread 调用时即使 trace 未传播也不会崩溃。
        """
        # 优先 contextvar（async 隔离），fallback 实例字段（threadpool 共享）
        trace = _current_trace_var.get() or self._thread_current
        if trace is None:
            # 软失败：返回 noop Span，避免业务阻塞
            # Why: LangGraph Send + RAG chain 在不同 thread 调用时，
            # trace 上下文可能丢失，硬抛错会让前端 SSE 流永远卡住。
            logger.debug(
                f"[Tracer] start_span('{span_id}') 但无 active trace — 返回 noop span"
            )
            noop = Span(
                span_id=span_id,
                parent_id=parent_id,
                name=name or span_id,
                type=type or _TYPE_INFER.get(span_id, "tool_call"),
                kind=kind or SpanKind.TOOL.value,
                start_time=_now_iso(),
                sequence=-1,
                input=input,
                status="skipped",
            )
            # P1-9: noop span 同样带 _t0（对齐下方注释），end_span 可正确计算
            # duration 而非恒为 0；标记 _noop 供调用方/测试识别丢弃的埋点。
            noop._t0 = time.time()
            noop._noop = True
            return noop
        now = _now_iso()
        # parent_id 未传 → 时间嵌套推断：优先取最近一个仍未关闭的 span（栈顶），
        # 其次 scope_root（子链嵌入模式），最后 root_span_id。
        # Why: chain 内部埋点（chunk_retrieval / enhanced_hybrid_retrieval / rerank 等）
        # 不传 parent_id，旧实现一律平铺到 scope_root/root，嵌套层级丢失，
        # 前端树形图与火焰图无法表达包含关系。
        if parent_id is None and span_id != trace.root_span_id:
            open_stack = getattr(trace, "_open_spans", None)
            if open_stack:
                parent_id = open_stack[-1].span_id
            else:
                scope_root = _scope_root_var.get()
                if scope_root is not None:
                    parent_id = scope_root
                else:
                    parent_id = trace.root_span_id or None
        # P1-9: parent_id 指向不存在的 span（如 skill 在 graph 之外执行时
        # 父 span 尚未创建），或误传了 Span 对象（不可哈希）→ 回退到 root，
        # 避免孤儿 span 导致 trace 树断裂
        if parent_id is not None and parent_id != trace.root_span_id:
            if not isinstance(parent_id, str):
                logger.debug(
                    f"[Tracer] start_span('{span_id}') parent_id 非字符串"
                    f"（{parent_id.__class__.__name__}），回退到 root span"
                )
                parent_id = trace.root_span_id or None
            elif parent_id not in {s.span_id for s in trace.spans}:
                logger.debug(
                    f"[Tracer] start_span('{span_id}') parent '{parent_id}' "
                    f"不存在，回退到 root span"
                )
                parent_id = trace.root_span_id or None

        # P1-7: sequence 改为 trace 内局部计数 —— 旧实现用 collector 实例级
        # 计数器，嵌套子 trace 的 start() 会重置/消耗序号，导致父 trace 的
        # 后续 span 拿到被污染的序号（如 reporter 拿到 seq=15）。
        seq = getattr(trace, "_seq", 0)
        trace._seq = seq + 1

        # P0-2: span_id 同 trace 内强制唯一 —— 多查询变体/重试会复用同一
        # span_id（如 enhanced_hybrid_retrieval 出现 3 次），前端按 span_id 建
        # 树/key 会冲突；自动追加 #N 后缀，原 id 保留在 metrics。
        existing_ids = {s.span_id for s in trace.spans}
        base_span_id = None
        if span_id in existing_ids:
            base_span_id = span_id
            n = 1
            while f"{span_id}#{n}" in existing_ids:
                n += 1
            span_id = f"{span_id}#{n}"

        span = Span(
            span_id=span_id,
            parent_id=parent_id,
            name=name or span_id,
            type=type or _TYPE_INFER.get(span_id, "tool_call"),
            kind=kind or SpanKind.TOOL.value,
            start_time=now,
            sequence=seq,
            input=input,
        )
        # 计时器直接绑在 Span 对象上（不再用 span_id 做 dict key）——
        # 同名 span_id（如中间件与 router 内部都叫 "router"）不会再互相覆盖，
        # 导致一方 duration 归零。noop span 同样带 t0，end_span 不会崩。
        span._t0 = time.time()
        if base_span_id is not None:
            span.metrics["base_span_id"] = base_span_id
        trace.spans.append(span)
        # P1-4: 维护开放 span 栈（后续无 parent 的 span 按时间嵌套挂到栈顶）
        open_stack = getattr(trace, "_open_spans", None)
        if open_stack is None:
            open_stack = []
            trace._open_spans = open_stack
        open_stack.append(span)
        span._stack_ref = open_stack  # end_span 跨线程也能正确出栈
        if parent_id is None:
            trace.root_span_id = span_id
        return span

    def end_span(self, span: Span, output: dict = None,
                 metrics: dict = None, status: str = "success"):
        """结束 Span：记录 end_time、计算 duration_ms、填充 metrics/output。"""
        if span.end_time:
            # 幂等守卫：已收口的 span 不重复覆盖（如 end_open_span 提前收口后，
            # 外层包装调用的 end_span 应为 no-op 而非把边界推迟）
            return
        t0 = getattr(span, "_t0", None)
        if t0 is not None:
            span.duration_ms = max(1, round((time.time() - t0) * 1000))
            span._t0 = None

        span.end_time = _now_iso()
        span.status = status
        if metrics:
            span.metrics.update(metrics)
        if output is not None:
            span.output = output

        # P1-4: 从开放栈移除（跨线程安全：栈引用存在 span 上）
        stack = getattr(span, "_stack_ref", None)
        if stack is not None:
            try:
                stack.remove(span)
            except ValueError:
                pass
            span._stack_ref = None

        # Phase 1.5: 通知 listener（用于 SSE 实时进度推送）
        trace = _current_trace_var.get() or self._thread_current
        if trace is not None and self._listeners:
            for cb in list(self._listeners):
                try:
                    cb(trace, span)
                except Exception:
                    logger.debug("trace listener 回调异常", exc_info=True)

    def end_open_span(self, span_id: str, metrics: dict = None,
                      status: str = "success") -> Span | None:
        """收口指定 span_id 的最近一个未关闭 span（精确边界收口）。

        Why: chain 顶层 "retrieval" span 曾包住整条 LCEL 链（含 LLM 生成），
        检索耗时虚增 10 倍以上。内部埋点在真实边界（context 就绪）调用本方法
        提前收口；外层原有的 end_span 调用因幂等守卫自动变为 no-op。
        """
        trace = _current_trace_var.get() or self._thread_current
        if trace is None:
            return None
        for span in reversed(trace.spans):
            if span.span_id == span_id and not span.end_time:
                self.end_span(span, metrics=metrics, status=status)
                return span
        return None

    def subscribe(self, callback) -> callable:
        """订阅 span end 事件。返回 unsubscribe() 函数（Phase 1.5 — 用于 SSE 推送）。

        callback 签名: (trace: TraceRecord, span: Span) -> None
        """
        self._listeners.append(callback)

        def _unsub():
            if callback in self._listeners:
                self._listeners.remove(callback)
        return _unsub

    def add_event(self, span: Span, name: str, level: str,
                  message: str, data: dict = None):
        """给 span 追加事件。level: debug|info|warn|error"""
        span.events.append({
            "name": name,
            "timestamp": _now_iso(),
            "level": level,
            "message": message,
            "attributes": data or {},
        })

    def finish(self, record: TraceRecord, answer: str, total_ms: int,
               model: str, provider: str = ""):
        """完成 trace 并持久化到 SQLite。"""
        record.model = model
        record.provider = provider
        record.answer_preview = answer[:200]
        record.answer_len = len(answer)
        record.total_ms = total_ms
        record.duration_ms = total_ms

        # P0-2: 强制关闭未收尾的 span（end_time 为空 = 埋点泄漏），
        # 标记 leaked 而非静默按 success 落库。
        leaked = self._close_leaked_spans(record)
        # P1-8: 折叠 0 价值 skipped span 到 root metrics，减少列表噪声。
        self._fold_skipped_spans(record)
        # P1-5: root 的 span_count 在折叠后与实际落库 spans 对齐
        #（旧值由 _end_root 在折叠前写入，常与真实条数差 1）
        root_span = next((s for s in record.spans if s.parent_id is None), None)
        if root_span is not None and "span_count" in root_span.metrics:
            root_span.metrics["span_count"] = len(record.spans) - 1
        # P0-4: root 归因度量 —— uncovered_ms 直接暴露 span 之间的无埋点黑洞。
        uncovered_ms = self._record_coverage(record)
        # P1-8: rejection 单一事实源 = metadata.rejection，root span 冗余详情瘦身。
        self._slim_rejection_dup(record)

        # P0-1: 顶层 status 聚合 — error > rejected > success。
        # 旧实现只看 error，Evidence Gate 拒答的 trace 被误标 success，
        # 污染 compute_metrics 的成功率统计。
        record.status = self._aggregate_status(record)

        self._aggregate_usage(record)
        # P0-2: llm_usage 明细回填 — token/成本/模型以按 trace_id 关联的调用
        # 明细为准（proxy 层每次 LLM 调用已同步落库），修复 ContextVar 跨线程
        # 丢失 + response_metadata 无 token_usage 导致的 usage 全 0 / model 空。
        self._backfill_usage_from_store(record)
        self._record_prometheus(record, leaked, uncovered_ms)

        try:
            from backend.prompts.service import collect_prompt_usage
            prompt_versions = collect_prompt_usage()
            if prompt_versions:
                record.metadata["prompt_versions"] = prompt_versions
        except Exception:
            pass

        # 嵌套恢复：子 trace 结束 → 把父 trace 还原为 current，
        # 外层流程后续 span 才不会落入 noop。
        with self._lock:
            parent = self._parents.pop(record.id, None)
            if self._thread_current is record:
                self._thread_current = parent
        if _current_trace_var.get() is record:
            _current_trace_var.set(parent)

        # Phase 3: 异步持久化（SQLite + Analytics 由后台 worker 批量写入）
        try:
            from backend.observability.trace_writer import get_trace_write_queue
            get_trace_write_queue().enqueue(record)
        except Exception:
            logger.warning("trace 异步入队失败", exc_info=True)
        # O2: 清除日志上下文
        from backend.shared.logger import clear_log_context
        clear_log_context()

    # =====================================================
    # 查询 API（直读 SQLite trace_store）
    # =====================================================

    def list(self, limit: int = 50, include_spans: bool = False) -> list[TraceRecord | dict]:
        """最近 N 条 trace（SQLite trace_store 直读）。

        Args:
            limit: 最多返回条数
            include_spans: True 时返回 TraceRecord 对象（spans 是 Span 列表），
                          用于测试断言。False 时返回 dict 列表（不含 spans 详情），
                          用于 API 列表渲染。
        """
        from backend.observability.trace_store import get_trace_store
        try:
            store = get_trace_store()
        except Exception:
            store = None

        try:
            rows = store.list(limit) if store else []
            if not include_spans:
                return rows
            # include_spans=True：重建 TraceRecord 对象，spans 转回 Span
            records = []
            for r in rows:
                rid = r.get("id") or r.get("trace_id")
                if not rid:
                    continue
                full = store.get(rid) or r
                records.append(self._dict_to_record(full))
            return records
        except Exception:
            logger.warning("trace 列表查询失败", exc_info=True)
            return []

    def _dict_to_record(self, d: dict) -> TraceRecord:
        """dict（含 spans 列表 of dict）→ TraceRecord（spans 是 Span 对象）。"""
        d = dict(d)  # copy
        spans_raw = d.pop("spans", []) or []
        d.pop("trace_id", None)  # TraceRecord 字段是 id
        rec = TraceRecord(**{k: v for k, v in d.items() if k in TraceRecord.__dataclass_fields__})
        rec.spans = [Span(**{k: v for k, v in s.items() if k in Span.__dataclass_fields__}) for s in spans_raw]
        return rec

    def list_active(self) -> list[dict]:
        """正在进行中的 trace：检查 contextvar + thread_local。
        返回简易 dict 列表（不包含 spans）。"""
        active = []
        trace = _current_trace_var.get() or self._thread_current
        if trace is not None:
            active.append({
                "id": trace.id,
                "question": trace.question,
                "workflow_name": trace.workflow_name,
                "duration_ms": trace.duration_ms,
                "status": "running",
            })
        return active

    def current(self) -> TraceRecord | None:
        """返回当前进行中的 trace（完整对象，含 spans）。

        业务代码无需调用；用于测试断言 in-flight trace 的 spans 状态。
        区别于 list()：list() 只从 SQLite 读已 finish() 的（不返回 spans），
        current() 读 contextvar 里的 in-flight trace（含 spans）。
        """
        return _current_trace_var.get() or self._thread_current

    def compute_metrics(self) -> dict:
        """聚合统计（SQLite trace_store 直读；含真实延迟分位数）。"""
        try:
            from backend.observability.trace_store import get_trace_store
            stored: list[dict] = get_trace_store().list(200)
        except Exception:
            logger.warning("trace 持久化存储查询失败", exc_info=True)
            stored = []
        active = len(self.list_active())
        total = len(stored) + active
        completed = [r for r in stored if r.get("duration_ms", 0) > 0]
        n = len(completed)
        durations = sorted(r.get("duration_ms", 0) / 1000 for r in completed)

        def _pct(q: float) -> float:
            if not durations:
                return 0
            idx = min(n - 1, int(q * n))
            return round(durations[idx], 3)

        return {
            "total_requests": total,
            "completed": n,
            "success": sum(1 for r in completed if r.get("status") == "success"),
            "error": sum(1 for r in completed if r.get("status") == "error"),
            "rejected": sum(1 for r in completed if r.get("status") == "rejected"),
            "aborted": 0,
            "active": active,
            "success_rate": round(sum(1 for r in completed if r.get("status") == "success") / n, 3) if n else 0,
            "rejected_rate": round(sum(1 for r in completed if r.get("status") == "rejected") / n, 3) if n else 0,
            "avg_elapsed_sec": round(sum(durations) / n, 3) if n else 0,
            "p50_elapsed_sec": _pct(0.50),
            "p95_elapsed_sec": _pct(0.95),
            "p99_elapsed_sec": _pct(0.99),
        }

    def get(self, trace_id: str):
        """获取单条 trace（SQLite trace_store 直读）。返回 dict 或 None。"""
        try:
            from backend.observability.trace_store import get_trace_store
            return get_trace_store().get(trace_id)
        except Exception:
            logger.debug("trace 详情查询失败: %s", trace_id, exc_info=True)
            return None

    def clear(self):
        """已无内存数据，此方法保留兼容不做操作。"""
        pass

    def clear_for_test(self):
        """测试辅助：重置全部内部状态。

        2d627d7 重构后 trace 持久化在 SQLite，内存中只剩 _thread_current /
        _span_seq / _listeners / _parents / contextvar。测试前后调用此方法确保隔离。

        注意：不会清空 SQLite 数据（如果用了临时 DB，tmp_path 会自动清理）。
        """
        with self._lock:
            self._thread_current = None
            self._span_seq = 0
            self._listeners.clear()
            self._parents.clear()
        _current_trace_var.set(None)

    # =====================================================
    # 内部 — finish() 数据质量处理（2026-09-03 数据记录优化）
    # =====================================================

    @staticmethod
    def _close_leaked_spans(record: TraceRecord) -> int:
        """强制关闭未收尾的 span。返回泄漏数量。

        检索层提前 return / 异常路径漏调 end_span 时，span 会以
        end_time='' + status='success' 的假象落库；这里补时间戳并改标 'leaked'
        （不覆盖 skipped/error/rejected 等显式状态）。
        """
        now = _now_iso()
        leaked = 0
        for s in record.spans:
            if s.end_time:
                continue
            t0 = getattr(s, "_t0", None)
            if t0 is not None:
                s.duration_ms = int((time.time() - t0) * 1000)
                s._t0 = None
            s.end_time = now
            if s.status == "success":
                s.status = "leaked"
            s.metrics["unclosed"] = True
            leaked += 1
        return leaked

    @staticmethod
    def _fold_skipped_spans(record: TraceRecord) -> None:
        """折叠 skipped span 到 root metrics（0ms 噪声不再占用 spans 数组）。

        metrics 一并保留到 skipped_stages，信息无损。
        """
        skipped = [s for s in record.spans if s.status == "skipped"]
        if not skipped:
            return
        root = next((s for s in record.spans if s.parent_id is None), None)
        if root is not None:
            root.metrics["skipped_stages"] = [
                {"span_id": s.span_id, "name": s.name, "metrics": s.metrics}
                for s in skipped
            ]
        record.spans = [s for s in record.spans if s.status != "skipped"]

    @staticmethod
    def _record_coverage(record: TraceRecord) -> int:
        """root span 耗时归因：covered = 直接子 span 之和，uncovered = 无埋点黑洞。

        只统计 root 直接子级（嵌套时长已包含在子级内），避免并行/嵌套重复累加。
        """
        root = next((s for s in record.spans if s.parent_id is None), None)
        if root is None or record.total_ms <= 0:
            return 0
        covered = sum(s.duration_ms for s in record.spans
                      if s.parent_id == root.span_id and s.duration_ms > 0)
        uncovered = max(0, record.total_ms - covered)
        root.metrics["covered_ms"] = covered
        root.metrics["uncovered_ms"] = uncovered
        return uncovered

    @staticmethod
    def _slim_rejection_dup(record: TraceRecord) -> None:
        """rejection 单一事实源 = metadata.rejection。

        root span metrics 只保留布尔索引位，删掉与 metadata 重复的
        reason/gate_layer 详情字段。
        """
        rej = record.metadata.get("rejection") or {}
        if not rej.get("rejected"):
            return
        for s in record.spans:
            if s.parent_id is None and s.metrics.get("rejected"):
                keep = {"rejected": True}
                for k in ("span_count", "skipped_stages", "covered_ms", "uncovered_ms"):
                    if k in s.metrics:
                        keep[k] = s.metrics[k]
                s.metrics = keep

    @staticmethod
    def _aggregate_status(record: TraceRecord) -> str:
        """顶层状态聚合：rejected > error > success。

        拒答优先于 error：Evidence Gate 拒答时，子 span 可能因检索失败
        标记了 error，但系统已优雅处理，整体应显示 rejected 而非 error。
        """
        if (record.metadata.get("rejection") or {}).get("rejected"):
            return "rejected"
        statuses = {s.status for s in record.spans}
        if "error" in statuses:
            return "error"
        if "rejected" in statuses:
            return "rejected"
        return "success"

    @staticmethod
    def _record_prometheus(record: TraceRecord, leaked: int, uncovered_ms: int) -> None:
        """trace 完成时上报数据质量指标（软失败）。"""
        try:
            from backend.observability.metrics import record_trace_finish
            rejection_layer = (record.metadata.get("rejection") or {}).get("layer") or ""
            ratio = (uncovered_ms / record.total_ms) if record.total_ms > 0 else 0.0
            record_trace_finish(record.status, rejection_layer, leaked, ratio)
        except Exception:
            logger.debug("[Tracer] Prometheus trace 指标记录失败", exc_info=True)

    # =====================================================
    # 内部
    # =====================================================

    @staticmethod
    def parse_tokens(result) -> dict:
        """从 LLM 返回值提取 token 计数字典。"""
        try:
            tu = {}
            if hasattr(result, "response_metadata") and result.response_metadata:
                tu = result.response_metadata.get("token_usage", {})
            if not tu and hasattr(result, "usage_metadata") and result.usage_metadata:
                tu = result.usage_metadata
            if not tu and hasattr(result, "llm_output") and result.llm_output:
                tu = result.llm_output.get("token_usage", {})
            p = tu.get("prompt_tokens", tu.get("input_tokens", 0))
            c = tu.get("completion_tokens", tu.get("output_tokens", 0))
            t = tu.get("total_tokens", p + c)
            if t:
                # 细粒度明细：缓存命中 / 推理 token（上游未返回时为 0）
                in_details = tu.get("input_token_details") or {}
                out_details = tu.get("output_token_details") or {}
                return {
                    "prompt_tokens": p, "completion_tokens": c, "total_tokens": t,
                    "cached_tokens": int(in_details.get("cache_read", 0) or 0),
                    "reasoning_tokens": int(out_details.get("reasoning", 0) or 0),
                }
        except Exception:
            logger.debug("token 用量解析失败", exc_info=True)
        return {}

    @staticmethod
    def _aggregate_usage(record: TraceRecord):
        """聚合 token 用量（含缓存/推理明细，逐 span 累加）。"""
        pt = ct = tt = cached = reasoning = 0
        for s in record.spans:
            m = s.metrics
            pt += m.get("prompt_tokens", 0)
            ct += m.get("completion_tokens", 0)
            tt += m.get("total_tokens", 0)
            cached += m.get("cached_tokens", 0) or 0
            reasoning += m.get("reasoning_tokens", 0) or 0
        if tt == 0 and (pt > 0 or ct > 0):
            tt = pt + ct
        if tt:
            record.usage = {
                "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt,
                "cached_tokens": cached, "reasoning_tokens": reasoning,
            }

    @staticmethod
    def _backfill_usage_from_store(record: TraceRecord):
        """以 llm_usage 明细表为准回填 usage / model / cost 与 llm span 指标。

        数据流：proxy 层每次 LLM 调用同步写入 llm_usage（带 trace_id），
        finish() 时本表数据已完整。span metrics 采集链（ContextVar →
        response_metadata）任一环节丢失时，本方法兜底，保证：
          - trace.usage / cost_usd 有真值（前端不再显示误导性 0）
          - trace.model / provider 有真值（调用方 finish 传空串的场景）
          - llm_call span 的 token/cost/model_name 指标补齐
        任何异常只降级不阻断（明细表禁用/为空时保持 _aggregate_usage 结果）。
        """
        try:
            from backend.observability.llm_usage_store import get_llm_usage_store
            rows = get_llm_usage_store().by_trace(record.id)
        except Exception:
            logger.debug("usage 回填：llm_usage 明细不可用", exc_info=True)
            return
        if not rows:
            return

        from datetime import datetime, timezone

        def _ts_key(iso: str) -> float:
            try:
                dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except Exception:
                return 0.0

        # ── 按组件聚合：usage 主口径只计 LLM 调用，embedding/rerank 进 by_component ──
        by_comp: dict[str, dict] = {}
        total_cost = 0.0
        for r in rows:
            comp = r.get("component") or "llm"
            agg = by_comp.setdefault(comp, {
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "reasoning_tokens": 0, "calls": 0,
            })
            agg["prompt_tokens"] += r.get("prompt_tokens") or 0
            agg["completion_tokens"] += r.get("completion_tokens") or 0
            agg["total_tokens"] += r.get("total_tokens") or 0
            agg["cached_tokens"] += r.get("cached_tokens") or 0
            agg["reasoning_tokens"] += r.get("reasoning_tokens") or 0
            agg["calls"] += 1
            total_cost += r.get("cost_usd") or 0.0

        llm_agg = by_comp.get("llm")
        if llm_agg and llm_agg["total_tokens"] > 0:
            record.usage = dict(llm_agg)
        record.usage["cost_usd"] = round(total_cost, 6)
        record.usage["by_component"] = by_comp
        record.cost_usd = round(total_cost, 6)

        # ── model / provider：调用方未显式传入时，取 LLM token 量最大的模型 ──
        if not (record.model or "").strip():
            llm_rows = [r for r in rows if (r.get("component") or "llm") == "llm"]
            if llm_rows:
                best = max(llm_rows, key=lambda r: r.get("total_tokens") or 0)
                record.model = best.get("model") or ""
                record.provider = best.get("provider") or ""

        # ── llm_call span 指标补齐：按时间最近邻配对（调用 ts ↔ span start）──
        llm_rows = sorted(
            (r for r in rows if (r.get("component") or "llm") == "llm"),
            key=lambda r: _ts_key(r.get("ts", "")),
        )
        if llm_rows:
            pending = [r for r in llm_rows if r.get("total_tokens")]
            llm_spans = [s for s in record.spans if s.type == "llm_call"]
            for span in llm_spans:
                has_tokens = (span.metrics.get("total_tokens")
                              or span.metrics.get("prompt_tokens")
                              or span.metrics.get("completion_tokens"))
                if has_tokens or not pending:
                    continue
                st = _ts_key(span.start_time)
                et = _ts_key(span.end_time) if span.end_time else None
                # 优先取落在 span 时间窗内的调用（LLM 行 ts ≈ 调用结束时刻），
                # 仅在无窗口内命中时才退化为全局最近邻（避免跨 span 错配）
                in_window = [r for r in pending
                             if _ts_key(r.get("ts", "")) >= st
                             and (et is None or _ts_key(r.get("ts", "")) <= et)]
                pool = in_window or pending
                row = min(pool, key=lambda r: abs(_ts_key(r.get("ts", "")) - st))
                pending.remove(row)
                span.metrics.update({
                    "prompt_tokens": row.get("prompt_tokens") or 0,
                    "completion_tokens": row.get("completion_tokens") or 0,
                    "total_tokens": row.get("total_tokens") or 0,
                    "cached_tokens": row.get("cached_tokens") or 0,
                    "reasoning_tokens": row.get("reasoning_tokens") or 0,
                    "cost_usd": row.get("cost_usd") or 0.0,
                    "model_name": row.get("model") or "",
                    "duration_ms_call": row.get("duration_ms") or 0.0,
                    "token_source": "llm_usage_backfill",
                })
                if span.output is None:
                    span.output = {}
                if isinstance(span.output, dict):
                    span.output.setdefault("model", row.get("model") or "")
                    span.output.setdefault("provider", row.get("provider") or "")


trace_collector = TraceCollector()


def current_trace_context() -> tuple[str, str]:
    """当前上下文活跃 trace 的 (trace_id, session_id)；无 trace 返回 ("", "")。

    供 proxy 等底层模块回填调用来源（llm_usage 明细按 trace_id 分组 per-turn），
    避免模块级循环依赖（proxy 在函数内导入本函数）。
    """
    try:
        trace = _current_trace_var.get() or trace_collector._thread_current
        if trace is not None:
            return trace.id, trace.session_id
    except Exception:
        pass
    return "", ""


__all__ = ["TraceCollector", "TraceRecord", "Span", "trace_collector", "MAX_TRACES",
           "current_trace_context"]
