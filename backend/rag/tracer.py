"""Pipeline Trace 收集 — 结构化接口，前端无需字符串解析"""
from __future__ import annotations

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
    request_id: str = ""     # 关联日志/前端
    timestamp: str = ""      # ISO8601
    session_id: str = ""
    model: str = ""
    provider: str = ""
    question: str = ""
    answer_preview: str = ""
    answer_len: int = 0
    duration_ms: int = 0
    total_ms: int = 0        # = duration_ms，兼容
    usage: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)        # {currency, amount}
    error: dict = field(default_factory=dict)       # {code, message}
    metadata: dict = field(default_factory=dict)    # 预留扩展
    steps: List[TraceStep] = field(default_factory=list)


class TraceCollector:
    """线程安全的 Tracing 收集器"""

    def __init__(self, max_size: int = MAX_TRACES):
        self._records: deque[TraceRecord] = deque(maxlen=max_size)
        self._current_trace: TraceRecord | None = None
        self._timers: dict[str, float] = {}

    def start(self, question: str, session_id: str = "default") -> TraceRecord:
        rid = uuid.uuid4().hex[:12]
        trace = TraceRecord(
            id=rid,
            request_id=rid,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            session_id=session_id,
            question=question,
        )
        self._current_trace = trace
        return trace

    def _start(self, step_id: str):
        self._timers[step_id] = time.time()

    def _end(self, step_id: str, label: str = "", metrics: dict = None, status: str = "success"):
        if step_id in self._timers:
            ms = int((time.time() - self._timers[step_id]) * 1000)
            if self._current_trace:
                self.add_step(self._current_trace, step_id, label, duration_ms=ms, metrics=metrics or {}, status=status)
            del self._timers[step_id]

    def add_step(self, record: TraceRecord, step_id: str, label: str, duration_ms: int = 0, metrics: dict = None, status: str = "success"):
        record.steps.append(TraceStep(id=step_id, label=label, duration_ms=duration_ms, metrics=metrics or {}, status=status))

    def finish(self, record: TraceRecord, answer: str, total_ms: int, model: str, provider: str = ""):
        record.model = model
        record.provider = provider
        record.answer_preview = answer[:80]
        record.answer_len = len(answer)
        record.total_ms = total_ms
        record.duration_ms = total_ms
        # 计算 duration_ratio
        if total_ms > 0:
            for s in record.steps:
                s.duration_ratio = round(s.duration_ms / total_ms, 3)
        # 聚合 usage
        pt = ct = tt = 0
        for s in record.steps:
            m = s.metrics
            pt += m.get("prompt_tokens", 0)
            ct += m.get("completion_tokens", 0)
            tt += m.get("total_tokens", 0)
        if tt:
            record.usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
        self._records.appendleft(record)

    @staticmethod
    def _parse_tokens(result) -> dict:
        """从 LLM 返回值提取 token 计数字典"""
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

    def list(self, limit: int = 50) -> list:
        return list(self._records)[:limit]

    def get(self, trace_id: str):
        for r in self._records:
            if r.id == trace_id:
                return r
        return None

    def clear(self):
        self._records.clear()


trace_collector = TraceCollector()
