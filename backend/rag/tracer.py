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
from collections import deque
from dataclasses import dataclass, field
from typing import List

MAX_TRACES = 200

# span_id → type 自动推断表（type 未传时使用）
_TYPE_INFER: dict[str, str] = {
    "llm_generate": "llm_call",
    "query_rewrite": "llm_call",
    "hybrid_retrieval": "retrieval",
    "retrieval": "retrieval",
    "rerank": "rerank",
    "mq_check": "tool_call",
    "citation": "tool_call",
    "faithfulness": "tool_call",
}


@dataclass
class Span:
    """通用 Span — 树形结构。

    type 用字符串而非枚举：新增类型不需要改后端 schema，
    前端按 type 路由渲染（retrieval→文档数，llm_call→token，rerank→阈值）。
    """
    span_id: str
    parent_id: str | None          # None = root span
    name: str                      # 人类可读名称
    type: str                      # llm_call | retrieval | rerank | agent | tool_call | workflow | ...
    status: str = "success"        # success | error | skipped
    start_time: str = ""
    end_time: str = ""
    duration_ms: int = 0
    sequence: int = 0              # 同 parent 下的排序序号
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
    error: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    spans: List[Span] = field(default_factory=list)
    root_span_id: str = ""
    workflow_name: str = ""
    sla_threshold_ms: int = 10000
    parent_id: str | None = None
    children_ids: List[str] = field(default_factory=list)
    graph: dict | None = None


class TraceCollector:
    """线程安全的 Tracing 收集器 — 统一 Span API。"""

    def __init__(self, max_size: int = MAX_TRACES):
        self._lock = threading.Lock()
        self._records: deque[TraceRecord] = deque(maxlen=max_size)
        self._current_trace: TraceRecord | None = None
        self._timers: dict[str, float] = {}
        self._span_seq: int = 0

    # =====================================================
    # 统一 API
    # =====================================================

    def start(self, question: str, session_id: str = "default",
              workflow_name: str = "rag_agent") -> TraceRecord:
        """开始一次新的 trace。线程安全。"""
        rid = uuid.uuid4().hex[:12]
        trace = TraceRecord(
            id=rid,
            request_id=rid,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            session_id=session_id,
            question=question,
            workflow_name=workflow_name,
        )
        with self._lock:
            self._current_trace = trace
            self._records.appendleft(trace)
            self._span_seq = 0
        return trace

    def start_span(self, span_id: str, parent_id: str | None = None,
                   name: str = "", type: str = "", input: dict = None) -> Span:
        """创建 Span 并开始计时。

        参数：
          span_id:    唯一标识（同 trace 内不重复）
          parent_id:  None=root span, 省略=自动取当前 trace 的 root_span_id
          name:       人类可读名称（省略用 span_id）
          type:       llm_call|retrieval|rerank|agent|tool_call|...（省略按 span_id 推断）
          input:      输入快照（可选）
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            trace = self._current_trace
            if trace is None:
                raise RuntimeError("start_span() 必须在 start() 之后调用")
            # parent_id 未传 → 取当前 root_span_id（必须已有 root）
            if parent_id is None and span_id != trace.root_span_id:
                parent_id = trace.root_span_id or None
            seq = self._span_seq
            self._span_seq += 1
            self._timers[span_id] = time.time()

        span = Span(
            span_id=span_id,
            parent_id=parent_id,
            name=name or span_id,
            type=type or _TYPE_INFER.get(span_id, "tool_call"),
            start_time=now,
            sequence=seq,
            input=input,
        )
        with self._lock:
            if self._current_trace:
                self._current_trace.spans.append(span)
                if parent_id is None:
                    self._current_trace.root_span_id = span_id
        return span

    def end_span(self, span: Span, output: dict = None,
                 metrics: dict = None, status: str = "success"):
        """结束 Span：记录 end_time、计算 duration_ms、填充 metrics/output。"""
        with self._lock:
            if span.span_id in self._timers:
                span.duration_ms = int((time.time() - self._timers[span.span_id]) * 1000)
                del self._timers[span.span_id]

        span.end_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        span.status = status
        if metrics:
            span.metrics.update(metrics)
        if output is not None:
            span.output = output

    def add_event(self, span: Span, name: str, level: str,
                  message: str, data: dict = None):
        """给 span 追加事件。level: debug|info|warn|error"""
        span.events.append({
            "name": name,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": level,
            "message": message,
            "attributes": data or {},
        })

    def finish(self, record: TraceRecord, answer: str, total_ms: int,
               model: str, provider: str = ""):
        """完成 trace。"""
        record.model = model
        record.provider = provider
        record.answer_preview = answer[:200]
        record.answer_len = len(answer)
        record.total_ms = total_ms
        record.duration_ms = total_ms

        self._aggregate_usage(record)

        with self._lock:
            self._current_trace = None

    # =====================================================
    # 查询 API
    # =====================================================

    def list(self, limit: int = 50) -> list:
        with self._lock:
            return list(self._records)[:limit]

    def list_active(self) -> list:
        with self._lock:
            return [r for r in self._records if r.duration_ms == 0]

    def compute_metrics(self) -> dict:
        with self._lock:
            all_records = list(self._records)

        completed = [r for r in all_records if r.duration_ms > 0]
        active = [r for r in all_records if r.duration_ms == 0]
        n = len(completed)
        if not n:
            return {
                "total_requests": len(all_records),
                "completed": 0, "active": len(active),
                "success_rate": 0, "avg_elapsed_sec": 0,
                "p50_elapsed_sec": 0, "p95_elapsed_sec": 0, "p99_elapsed_sec": 0,
            }

        latencies_ms = sorted([r.duration_ms for r in completed])

        def _is_error(r):
            return any(s.status == "error" for s in r.spans)

        success_count = sum(1 for r in completed if not _is_error(r))
        return {
            "total_requests": len(all_records),
            "completed": n,
            "success": success_count,
            "error": n - success_count,
            "aborted": 0,
            "active": len(active),
            "success_rate": round(success_count / n, 3) if n > 0 else 0,
            "avg_elapsed_sec": round(sum(latencies_ms) / n / 1000, 1) if n else 0,
            "p50_elapsed_sec": round(latencies_ms[int(n * 0.50)] / 1000, 1) if n > 0 else 0,
            "p95_elapsed_sec": round(latencies_ms[min(int(n * 0.95), n - 1)] / 1000, 1) if n > 0 else 0,
            "p99_elapsed_sec": round(latencies_ms[min(int(n * 0.99), n - 1)] / 1000, 1) if n > 0 else 0,
        }

    def get(self, trace_id: str):
        with self._lock:
            for r in self._records:
                if r.id == trace_id:
                    return r
        return None

    def clear(self):
        with self._lock:
            self._records.clear()

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
                return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": t}
        except Exception:
            pass
        return {}

    @staticmethod
    def _aggregate_usage(record: TraceRecord):
        """聚合 token 用量。"""
        pt = ct = tt = 0
        for s in record.spans:
            m = s.metrics
            pt += m.get("prompt_tokens", 0)
            ct += m.get("completion_tokens", 0)
            tt += m.get("total_tokens", 0)
        if tt == 0 and (pt > 0 or ct > 0):
            tt = pt + ct
        if tt:
            record.usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}


trace_collector = TraceCollector()

__all__ = ["TraceCollector", "TraceRecord", "Span", "trace_collector", "MAX_TRACES"]
