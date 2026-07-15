"""Pipeline Trace 收集 — 线程安全，结构化接口，前端无需字符串解析"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import List

MAX_TRACES = 200


@dataclass
class TraceStep:
    id: str           # 稳定标识，如 "llm_generate"
    label: str        # 中文显示名，如 "LLM生成"
    duration_ms: int = 0
    duration_ratio: float = 0.0
    status: str = "success"    # success | error | skipped
    metrics: dict = field(default_factory=dict)


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
    steps: List[TraceStep] = field(default_factory=list)


class TraceCollector:
    """线程安全的 Tracing 收集器。"""

    def __init__(self, max_size: int = MAX_TRACES):
        self._lock = threading.Lock()
        self._records: deque[TraceRecord] = deque(maxlen=max_size)
        self._current_trace: TraceRecord | None = None
        self._timers: dict[str, float] = {}

    # =====================================================
    # 公共 API
    # =====================================================

    def start(self, question: str, session_id: str = "default") -> TraceRecord:
        """开始一次新的 trace。线程安全。"""
        rid = uuid.uuid4().hex[:12]
        trace = TraceRecord(
            id=rid,
            request_id=rid,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            session_id=session_id,
            question=question,
        )
        with self._lock:
            self._current_trace = trace
        return trace

    def start_step(self, step_id: str):
        """开始计时一个步骤。"""
        with self._lock:
            self._timers[step_id] = time.time()

    def end_step(self, step_id: str, label: str = "", metrics: dict = None,
                 status: str = "success"):
        """结束计时并记录步骤。"""
        with self._lock:
            if step_id not in self._timers:
                return
            ms = int((time.time() - self._timers[step_id]) * 1000)
            trace = self._current_trace
            del self._timers[step_id]

        if trace:
            self.add_step(trace, step_id, label, duration_ms=ms,
                          metrics=metrics or {}, status=status)

    def add_step(self, record: TraceRecord, step_id: str, label: str,
                 duration_ms: int = 0, metrics: dict = None, status: str = "success"):
        record.steps.append(TraceStep(
            id=step_id, label=label, duration_ms=duration_ms,
            metrics=metrics or {}, status=status,
        ))

    def finish(self, record: TraceRecord, answer: str, total_ms: int,
               model: str, provider: str = ""):
        """完成 trace 并归档。"""
        record.model = model
        record.provider = provider
        record.answer_preview = answer[:200]
        record.answer_len = len(answer)
        record.total_ms = total_ms
        record.duration_ms = total_ms

        self._compute_ratios(record, total_ms)
        self._aggregate_usage(record)

        with self._lock:
            self._records.appendleft(record)
            self._current_trace = None

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

    # =====================================================
    # 查询 API
    # =====================================================

    def list(self, limit: int = 50) -> list:
        with self._lock:
            return list(self._records)[:limit]

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
    def _compute_ratios(record: TraceRecord, total_ms: int):
        if total_ms > 0:
            for s in record.steps:
                s.duration_ratio = round(s.duration_ms / total_ms, 3)

    @staticmethod
    def _aggregate_usage(record: TraceRecord):
        pt = ct = tt = 0
        for s in record.steps:
            m = s.metrics
            pt += m.get("prompt_tokens", 0)
            ct += m.get("completion_tokens", 0)
            tt += m.get("total_tokens", 0)
        if tt:
            record.usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}


trace_collector = TraceCollector()
