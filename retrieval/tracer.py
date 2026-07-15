"""Pipeline Trace 收集 + LangChain Callback 打点"""
from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler

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

    def start(self, question: str, session_id: str = "default") -> TraceRecord:
        return TraceRecord(
            id=uuid.uuid4().hex[:12],
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            session_id=session_id,
            model="?",
            question=question,
            answer_preview="",
            answer_len=0,
            total_ms=0,
        )

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


# =====================================================
# LangChain Callback — 非侵入式捕获每一步耗时
# =====================================================

class TraceCallback(BaseCallbackHandler):
    """注册到 chain.invoke(config={"callbacks": [...]}) 中，自动捕获每步耗时"""

    def __init__(self, trace: TraceRecord):
        self.trace = trace
        self._timers: Dict[str, float] = {}
        self._ctx: Dict[str, Any] = {}  # 跨步骤上下文

    def _start(self, key: str):
        self._timers[key] = time.time()

    def _end(self, key: str, name: str, detail: str = "", hits: str = ""):
        if key in self._timers:
            ms = int((time.time() - self._timers[key]) * 1000)
            trace_collector.add_step(self.trace, name, detail, hits=hits, elapsed_ms=ms)
            del self._timers[key]

    # ── Retriever ──────────────────────────

    def on_retriever_start(self, serialized: Dict[str, Any], query: str, **kwargs):
        name = (serialized.get("name") or serialized.get("id") or [""])[-1] if isinstance(serialized.get("id"), list) else "检索"
        self._start("retriever")

    def on_retriever_end(self, documents: list, **kwargs):
        self._end("retriever", "检索", hits=f"{len(documents)}docs")

    # ── LLM ───────────────────────────────

    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs):
        name = (serialized.get("name") or serialized.get("id") or [""])[-1] if isinstance(serialized.get("id"), list) else "LLM"
        self._start("llm")
        # 记录第一个 prompt 的前几个字作为上下文
        if prompts:
            self._ctx["llm_hint"] = prompts[0][:50]

    def on_llm_end(self, response, **kwargs):
        hint = self._ctx.pop("llm_hint", "")
        detail = hint if hint else ""
        # 尝试从 response 拿 token 数
        tokens = ""
        try:
            usage = getattr(response, "llm_output", {}) or {}
            if "token_usage" in usage:
                tu = usage["token_usage"]
                tokens = f"{tu.get('total_tokens', '?')}tokens"
            elif hasattr(response, "response_metadata"):
                rm = response.response_metadata
                if "token_usage" in rm:
                    tokens = f"{rm['token_usage'].get('total_tokens', '?')}tokens"
        except Exception:
            pass
        self._end("llm", "LLM生成", detail=detail, hits=tokens)

    # ── Chain (stuff_chain / overall) ─────

    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs):
        name = (serialized.get("name") or serialized.get("id") or [""])[-1] if isinstance(serialized.get("id"), list) else ""
        if "StuffDocuments" in str(serialized):
            self._start("stuff")

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs):
        pass  # StuffDocuments is covered by LLM callback
