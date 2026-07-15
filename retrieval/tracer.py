"""Pipeline Trace 收集 + LangChain Callback 打点"""
from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import List

MAX_TRACES = 200


@dataclass
class TraceStep:
    name: str
    detail: str = ""
    hits: str = ""           # 兼容旧前端，逐步废弃
    elapsed_ms: int = 0
    metrics: dict = field(default_factory=dict)  # 结构化数据，新前端用


@dataclass
class TraceRecord:
    id: str
    timestamp: str
    session_id: str
    model: str
    question: str
    answer_preview: str
    answer_len: int
    total_ms: int
    steps: List[TraceStep] = field(default_factory=list)


class TraceCollector:
    """线程安全的 Tracing 收集器，内存滚动存储"""

    def __init__(self, max_size: int = MAX_TRACES):
        self._records: deque[TraceRecord] = deque(maxlen=max_size)
        self._current_trace: TraceRecord | None = None
        self._timers: dict[str, float] = {}

    def start(self, question: str, session_id: str = "default") -> TraceRecord:
        trace = TraceRecord(
            id=uuid.uuid4().hex[:12],
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            session_id=session_id,
            model="?",
            question=question,
            answer_preview="",
            answer_len=0,
            total_ms=0,
        )
        self._current_trace = trace
        return trace

    def _start(self, name: str):
        """开始计时（业务层埋点调用）"""
        self._timers[name] = time.time()

    def _end(self, name: str, detail: str = "", hits: str = "", metrics: dict = None):
        """结束计时，添加步骤。metrics 为结构化字段，前端无需字符串解析。"""
        if name in self._timers:
            ms = int((time.time() - self._timers[name]) * 1000)
            if self._current_trace:
                self.add_step(self._current_trace, name, detail, hits=hits, elapsed_ms=ms, metrics=metrics or {})
            del self._timers[name]

    @staticmethod
    def _extract_tokens(result) -> str:
        """从 LLM 返回值提取 token 数：prompt/completion/total"""
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
                return f"P{p}|C{c}|T{t}"
        except Exception:
            pass
        return ""
        """结束计时，添加步骤"""
        if name in self._timers:
            ms = int((time.time() - self._timers[name]) * 1000)
            if self._current_trace:
                self.add_step(self._current_trace, name, detail, hits=hits, elapsed_ms=ms)
            del self._timers[name]

    def add_step(self, record: TraceRecord, name: str, detail: str = "", hits: str = "", elapsed_ms: int = 0, metrics: dict = None):
        record.steps.append(TraceStep(name=name, detail=detail, hits=hits, elapsed_ms=elapsed_ms, metrics=metrics or {}))

    def finish(self, record: TraceRecord, answer: str, total_ms: int, model: str):
        record.model = model
        record.answer_preview = answer[:80]
        record.answer_len = len(answer)
        record.total_ms = total_ms
        self._records.appendleft(record)

    def list(self, limit: int = 50) -> list:
        return list(self._records)[:limit]

    def get(self, trace_id: str):
        for r in self._records:
            if r.id == trace_id:
                return r
        return None

    def clear(self):
        self._records.clear()


# 全局单例
trace_collector = TraceCollector()


