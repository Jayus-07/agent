"""可观测性 — TraceStore（内存环形缓冲区）+ 图拓扑定义

TraceStore 用 threading.Lock（跨 ThreadPoolExecutor + asyncio 两种并发模型）。
每次 stream_events() 调用产出的事件镜像写入 TraceStore，
监控面板通过 REST API 轮询查询。
"""

import os
import time
import uuid
import threading
from collections import deque
from dataclasses import dataclass, field


# =====================================================
# 数据类
# =====================================================

@dataclass
class TraceEvent:
    """单条追踪事件（SSE 事件的镜像）"""
    event: str         # "status" | "log" | "delta" | "done" | "error"
    data: dict
    ts: float


@dataclass
class Trace:
    """单次请求的完整追踪"""
    trace_id: str
    session_id: str
    question: str
    kb_id: str
    start_time: float
    end_time: float | None = None
    status: str = "running"  # running | success | error | aborted
    events: list = field(default_factory=list)  # list[TraceEvent]
    alerts: list = field(default_factory=list)  # list[dict]
    final_answer_len: int = 0


# =====================================================
# TraceStore — 线程安全环形缓冲区
# =====================================================

class TraceStore:
    """线程安全的内存环形缓冲区，存储最近 N 条请求的完整 trace"""

    def __init__(self, max_traces: int = 200):
        self._traces: deque[Trace] = deque(maxlen=max_traces)
        self._by_id: dict[str, Trace] = {}
        self._lock = threading.Lock()

    # ── 生命周期 ──

    def start_trace(self, session_id: str, question: str, kb_id: str = "default") -> Trace:
        """开始一条新 trace，返回 trace 对象"""
        trace_id = uuid.uuid4().hex[:12]
        trace = Trace(
            trace_id=trace_id,
            session_id=session_id,
            question=question[:200],  # 截断长问题，减少内存
            kb_id=kb_id,
            start_time=time.time(),
        )
        with self._lock:
            self._by_id[trace_id] = trace
            self._traces.append(trace)
        return trace

    def add_event(self, trace_id: str, event_type: str, data: dict):
        """追加一条事件到 trace"""
        with self._lock:
            trace = self._by_id.get(trace_id)
            if trace:
                trace.events.append(TraceEvent(
                    event=event_type,
                    data=dict(data),  # 浅拷贝，避免引用污染
                    ts=time.time(),
                ))

    def end_trace(self, trace_id: str, status: str = "success",
                  final_answer_len: int = 0, alerts: list = None):
        """标记 trace 结束"""
        with self._lock:
            trace = self._by_id.get(trace_id)
            if trace:
                trace.end_time = time.time()
                trace.status = status
                trace.final_answer_len = final_answer_len
                if alerts:
                    trace.alerts = list(alerts)

    # ── 查询 ──

    def get_recent(self, limit: int = 20) -> list[dict]:
        """最近 N 条 trace 摘要（不含 events，减少 payload）"""
        with self._lock:
            traces = list(self._traces)[-limit:]
        return [_trace_summary(t) for t in reversed(traces)]

    def get_trace(self, trace_id: str) -> dict | None:
        """获取单条 trace 的完整详情"""
        with self._lock:
            trace = self._by_id.get(trace_id)
        if trace is None:
            return None
        return _trace_detail(trace)

    def get_active(self) -> list[dict]:
        """当前活跃的 trace（status=running）"""
        with self._lock:
            active = [t for t in self._traces if t.status == "running"]
        return [_trace_summary(t) for t in reversed(active)]

    def get_aggregated_metrics(self) -> dict:
        """计算聚合指标：成功率、P50/P95/P99 延迟"""
        with self._lock:
            traces = list(self._traces)

        completed = [t for t in traces if t.end_time is not None and t.status != "running"]
        if not completed:
            return {
                "total_requests": len(traces),
                "completed": 0,
                "active": len([t for t in traces if t.status == "running"]),
                "success_rate": 0,
                "avg_elapsed_sec": 0,
                "p50_elapsed_sec": 0,
                "p95_elapsed_sec": 0,
                "p99_elapsed_sec": 0,
            }

        latencies = sorted([t.end_time - t.start_time for t in completed])
        n = len(latencies)
        success_count = sum(1 for t in completed if t.status == "success")

        return {
            "total_requests": len(traces),
            "completed": n,
            "success": success_count,
            "error": sum(1 for t in completed if t.status == "error"),
            "aborted": sum(1 for t in completed if t.status == "aborted"),
            "success_rate": round(success_count / n, 3) if n > 0 else 0,
            "active": len([t for t in traces if t.status == "running"]),
            "avg_elapsed_sec": round(sum(latencies) / n, 1) if n else 0,
            "p50_elapsed_sec": round(latencies[int(n * 0.50)], 1) if n > 0 else 0,
            "p95_elapsed_sec": round(latencies[min(int(n * 0.95), n - 1)], 1) if n > 0 else 0,
            "p99_elapsed_sec": round(latencies[min(int(n * 0.99), n - 1)], 1) if n > 0 else 0,
        }

    def clear(self):
        """清空所有 trace（用于测试/重置）"""
        with self._lock:
            self._traces.clear()
            self._by_id.clear()


# ── 辅助函数 ──

def _trace_summary(t: Trace) -> dict:
    return {
        "trace_id": t.trace_id,
        "session_id": t.session_id,
        "question": t.question,
        "kb_id": t.kb_id,
        "start_time": t.start_time,
        "end_time": t.end_time,
        "elapsed_sec": round(t.end_time - t.start_time, 2) if t.end_time else None,
        "status": t.status,
        "event_count": len(t.events),
        "final_answer_len": t.final_answer_len,
        "alert_count": len(t.alerts),
    }


def _trace_detail(t: Trace) -> dict:
    d = _trace_summary(t)
    # 从 status 事件提取节点访问时序
    d["nodes_visited"] = []
    for e in t.events:
        if e.event == "status":
            d["nodes_visited"].append({
                "node": e.data.get("node", ""),
                "ts": e.ts,
            })
    # 返回完整事件列表
    d["events"] = [
        {"event": e.event, "data": e.data, "ts": e.ts}
        for e in t.events
    ]
    return d


# =====================================================
# 图拓扑静态定义（供前端渲染）
# =====================================================

GRAPH_TOPOLOGY = {
    "nodes": [
        {"id": "planner",       "label": "任务规划",   "type": "llm"},
        {"id": "critique",      "label": "计划审查",   "type": "llm"},
        {"id": "supervisor",    "label": "调度决策",   "type": "router"},
        {"id": "sql_worker",    "label": "数据库查询", "type": "worker"},
        {"id": "rag_worker",    "label": "知识库检索", "type": "worker"},
        {"id": "report_worker", "label": "报告生成",   "type": "worker"},
        {"id": "reporter",      "label": "结果汇总",   "type": "llm"},
    ],
    "edges": [
        {"id": "e1",  "source": "planner",       "target": "critique",      "label": ""},
        {"id": "e2",  "source": "critique",       "target": "supervisor",   "label": "有任务"},
        {"id": "e3",  "source": "critique",       "target": "reporter",     "label": "空计划"},
        {"id": "e4",  "source": "supervisor",     "target": "sql_worker",   "label": "dispatch"},
        {"id": "e5",  "source": "supervisor",     "target": "rag_worker",   "label": "dispatch"},
        {"id": "e6",  "source": "supervisor",     "target": "report_worker","label": "dispatch"},
        {"id": "e7",  "source": "sql_worker",     "target": "supervisor",   "label": "完成"},
        {"id": "e8",  "source": "rag_worker",     "target": "supervisor",   "label": "完成"},
        {"id": "e9",  "source": "report_worker",  "target": "supervisor",   "label": "完成"},
        {"id": "e10", "source": "supervisor",     "target": "reporter",     "label": "全部完成"},
    ],
}

# 节点标签映射（与 graph.py _NODE_LABELS 保持一致）
NODE_LABELS = {
    "planner":       "任务规划",
    "critique":      "计划审查",
    "supervisor":    "调度决策",
    "sql_worker":    "数据库查询",
    "rag_worker":    "知识库检索",
    "report_worker": "报告生成",
    "reporter":      "结果汇总",
}

# =====================================================
# 全局单例
# =====================================================

trace_store = TraceStore(max_traces=int(os.getenv("TRACE_BUFFER_SIZE", "200")))
