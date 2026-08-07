"""
E2E Demo: Planner → SQL → Business Analysis → Report
端到端演示 "哪些商品库存不足？分析风险并给出补货建议。"

运行: cd backend && python e2e_demo.py
"""
import json
import sys
import time
from pathlib import Path

# 确保 agent/ 目录在 sys.path 中
_AGENT_DIR = str(Path(__file__).resolve().parent.parent)
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from backend.orchestration.graph.system import MultiAgentSystem
from backend.observability.tracer import trace_collector
from backend.shared.logger import logger


def main():
    print("=" * 70)
    print("  E2E Demo: Planner → SQL → Business Analysis → Report")
    print("=" * 70)

    # ── 1. 系统初始化 ──
    print("\n[1/5] 初始化 MultiAgentSystem...")
    t0 = time.time()
    system = MultiAgentSystem()
    print(f"  就绪 (耗时 {(time.time()-t0)*1000:.0f}ms)")

    # ── 2. 用户问题 ──
    question = "哪些商品库存不足？分析风险并给出补货建议。"
    print(f"\n[2/5] 用户问题: {question}")
    print(f"  Planner 应生成: sql.query → business.analyze → report.generate")

    # ── 3. 执行完整管线 ──
    print("\n[3/5] 执行 Multi-Agent 管线...")
    print(f"  (Planner→Critique→Supervisor→Skills→Reporter)")
    t1 = time.time()
    try:
        answer = system.ask(question, session_id="e2e-demo", kb_id="default")
        elapsed = time.time() - t1
        print(f"  管线完成 (总耗时 {elapsed:.1f}s)")
    except Exception as e:
        print(f"  管线执行异常: {e}")
        import traceback
        traceback.print_exc()
        return

    # ── 4. 最终回答 ──
    print("\n[4/5] Reporter 最终输出:")
    print("-" * 50)
    # 截断过长的输出
    max_len = 3000
    print(answer[:max_len])
    if len(answer) > max_len:
        print(f"\n  ... (输出截断，总长 {len(answer)} 字符)")

    # ── 5. Trace 可视化 ──
    print(f"\n[5/5] Trace 记录:")
    traces = trace_collector.list(limit=5)
    for t in traces:
        # trace_collector.list() 返回 dict（JSON 序列化后的格式）
        tid = t.get("id", "?")
        print(f"  TraceID: {tid}")
        print(f"  Question: {t.get('question', '')[:80]}...")
        spans = t.get("spans", [])
        print(f"  Spans: {len(spans)}")
        for sp in spans:
            label = sp.get("name") or sp.get("span_id", "?")
            status = sp.get("status", "?")
            status_icon = {"success": "✅", "error": "❌", "skipped": "⏭️"}.get(status, "❓")
            metrics = sp.get("metrics", {})
            metrics_str = ""
            if metrics:
                m = {k: v for k, v in metrics.items() if v is not None}
                if m:
                    metrics_str = f" — {json.dumps(m, ensure_ascii=False)}"
            print(f"    {status_icon} [{status}] {label}{metrics_str}")

    print("\n" + "=" * 70)
    print("  Demo 完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
