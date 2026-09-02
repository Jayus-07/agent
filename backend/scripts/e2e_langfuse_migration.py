"""Langfuse 迁移端到端验证脚本（一次性，验证后可删）。

链路：TraceCollector.start → start_span × N → finish（上报 Langfuse + SQLite 兜底）
     → collector.list/get（Langfuse 读路径）→ 校验字段对等。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from dotenv import load_dotenv
load_dotenv("backend/.env")

from backend.observability.tracer import trace_collector, SpanKind
from backend.observability.langfuse_exporter import get_langfuse_exporter

ex = get_langfuse_exporter()
print(f"[1] exporter enabled={ex.enabled} host={ex.host}")
assert ex.enabled, "exporter 未启用，检查 .env 配置"

# ── 构造一条典型 RAG trace ──
trace = trace_collector.start("Langfuse 迁移验证：退货政策是什么", session_id="e2e-sess-1",
                              workflow_name="rag_agent", workflow_kind="rag_query")
trace.sla_threshold_ms = 30000
trace.tags = {"kb_id": "kb-test"}

root = trace_collector.start_span("root", parent_id=None, name="RAG 智能问答",
                                  type="agent", input={"question": "退货政策是什么"})
ret = trace_collector.start_span("retrieval", parent_id="root", name="检索",
                                 type="retrieval", input={"top_k": 5})
time.sleep(0.12)
trace_collector.end_span(ret, output={"hits": 3}, metrics={"top_k": 5, "elapsed_ms": 120})
llm = trace_collector.start_span("llm_generate", parent_id="root", name="生成",
                                 type="llm_call", input={"prompt": "..."})
time.sleep(0.15)
trace_collector.end_span(llm, output={"response": "七天无理由退货"},
                         metrics={"model_name": "qwen-plus", "prompt_tokens": 120,
                                  "completion_tokens": 30, "total_tokens": 150})
trace_collector.end_span(root, output={"answer_len": 7},
                         metrics={"span_count": 2})
trace_collector.finish(trace, "七天无理由退货", 280, "qwen-plus", "dashscope")
print(f"[2] finish 完成 trace_id={trace.id}")

# ── 等待 Langfuse ingestion 落库 ──
got = None
for i in range(12):
    time.sleep(2)
    got = ex.get_trace(trace.id)
    if got:
        break
print(f"[3] Langfuse 读回: {'成功' if got else '失败'} (等待 {(i+1)*2}s)")
assert got, "Langfuse 读回失败"

# ── 字段对等校验 ──
assert got["id"] == trace.id
assert got["question"] == "Langfuse 迁移验证：退货政策是什么"
assert got["session_id"] == "e2e-sess-1"
assert got["workflow_name"] == "rag_agent"
assert got["model"] == "qwen-plus"
assert got["usage"]["total_tokens"] == 150
assert len(got["spans"]) == 3, f"spans={len(got['spans'])}"
by_id = {s["span_id"]: s for s in got["spans"]}
assert by_id["llm_generate"]["type"] == "llm_call"
assert by_id["llm_generate"]["parent_id"] == "root"
assert by_id["llm_generate"]["metrics"]["total_tokens"] == 150
assert by_id["retrieval"]["duration_ms"] >= 100, by_id["retrieval"]["duration_ms"]
print("[4] 字段对等校验通过（question/session/model/usage/3 spans/父子链/耗时）")

# ── collector 读路径（经 Langfuse） ──
rows = trace_collector.list(20)
assert any(r.get("id") == trace.id for r in rows), "list() 未返回新 trace"
detail = trace_collector.get(trace.id)
assert detail and len(detail["spans"]) == 3
metrics = trace_collector.compute_metrics()
assert metrics["completed"] >= 1 and metrics["success_rate"] > 0
print(f"[5] collector.list/get/compute_metrics 经 Langfuse 读路径正常"
      f"（completed={metrics['completed']}, success_rate={metrics['success_rate']}）")
print(f"\nE2E PASS — Langfuse UI: {ex.host} → project agent-rag → trace {trace.id}")
