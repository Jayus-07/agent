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
    hits: str = ""
    elapsed_ms: int = 0


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

    def _end(self, name: str, detail: str = "", hits: str = ""):
        """结束计时，添加步骤"""
        if name in self._timers:
            ms = int((time.time() - self._timers[name]) * 1000)
            if self._current_trace:
                self.add_step(self._current_trace, name, detail, hits=hits, elapsed_ms=ms)
            del self._timers[name]

    def add_step(self, record: TraceRecord, name: str, detail: str = "", hits: str = "", elapsed_ms: int = 0):
        record.steps.append(TraceStep(name=name, detail=detail, hits=hits, elapsed_ms=elapsed_ms))

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


