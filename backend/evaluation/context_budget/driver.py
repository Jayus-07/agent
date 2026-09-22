"""context_budget.driver — Phase 4 实机 E2E 驱动（HTTP → APISIX → chat/stream）

可重复执行：同一条命令重跑得到新数据；不 mock，不直调 Python 函数。
输出结构化事件流（context/status/done/error）供上层统计。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

def _default_base() -> str:
    if os.getenv("P4_BASE"):
        return os.getenv("P4_BASE")
    # 容器内运行（无 9080 映射）：直连本容器 app :8000，无 /api 前缀
    if os.path.exists("/.dockerenv"):
        return "http://127.0.0.1:8000"
    return "http://127.0.0.1:9080"


BASE = _default_base()
_API_KEY = os.getenv("API_KEY", "")
if not _API_KEY:
    # 本机直跑：从仓库根 .env 读（容器外无该环境变量）
    from pathlib import Path
    _env = Path(__file__).resolve().parents[3] / ".env"
    if _env.exists():
        for line in _env.read_text(encoding="utf-8").splitlines():
            if line.startswith("API_KEY="):
                _API_KEY = line.split("=", 1)[1].strip()
                break


@dataclass
class StreamResult:
    session_id: str
    answer: str = ""
    done: dict = field(default_factory=dict)
    context_events: list[dict] = field(default_factory=list)
    status_nodes: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed_s: float = 0.0
    ttft_s: float | None = None      # 首 delta 时间
    llm_calls: int = 0               # 近似：planner/critique 等节点计数不足，仅作参考
    raw_events: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "elapsed_s": round(self.elapsed_s, 2),
            "ttft_s": round(self.ttft_s, 3) if self.ttft_s else None,
            "context_events": self.context_events,
            "context_usage": self.done.get("context_usage"),
            "nodes": self.status_nodes,
            "error": self.error,
            "answer_head": self.answer[:80],
        }


def _chat_url() -> str:
    """APISIX(9080) 走 /api 前缀（网关 rewrite 剥离）；容器内直连 app :8000 无前缀。"""
    prefix = "/api" if ":9080" in BASE else ""
    return f"{BASE}{prefix}/chat/stream"


def chat_stream(
    question: str,
    session_id: str,
    *,
    user_id: str = "p4-eval",
    domain_hint: str | None = None,
    kb_id: str | None = None,
    timeout: float = 300.0,
) -> StreamResult:
    """发一条真实 /api/chat/stream 请求并收集 SSE 事件。"""
    result = StreamResult(session_id=session_id)
    payload: dict[str, Any] = {
        "question": question, "session_id": session_id, "user_id": user_id,
    }
    if domain_hint:
        payload["domain_hint"] = domain_hint
    if kb_id:
        payload["kb_id"] = kb_id

    start = time.perf_counter()
    try:
        resp = requests.post(
            _chat_url(), json=payload,
            headers={"X-API-Key": _API_KEY, "X-User-Id": user_id},
            stream=True, timeout=timeout,
        )
        if resp.status_code != 200:
            result.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            result.elapsed_s = time.perf_counter() - start
            return result

        event_name = None
        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            if raw_line.startswith("event:"):
                event_name = raw_line[len("event:"):].strip()
                continue
            if raw_line.startswith("data:"):
                try:
                    data = json.loads(raw_line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue
                result.raw_events.append(
                    {"event": event_name, "data": data})
                if event_name == "delta":
                    if result.ttft_s is None:
                        result.ttft_s = time.perf_counter() - start
                    result.answer += data.get("content", "")
                elif event_name == "context":
                    result.context_events.append(data)
                elif event_name == "status":
                    result.status_nodes.append(data.get("node", ""))
                elif event_name == "done":
                    result.done = data
                elif event_name == "error":
                    result.error = data.get("message", "")[:300]
                event_name = None
    except requests.RequestException as e:
        result.error = f"request failed: {e}"
    result.elapsed_s = time.perf_counter() - start
    return result


def multi_turn(questions: list[str], session_id: str, **kw) -> list[StreamResult]:
    return [chat_stream(q, session_id, **kw) for q in questions]
