# -*- coding: utf-8 -*-
"""Phase 4 e2e 真实样例（临时脚本，不进仓库）。

两场景：
1. 持久化降级可见化（任务书 §10）：memory 后端 → degraded 三态贯通
   （state / supervisor_decision / 行程单披露）+ TRAVEL_REQUIRE_PERSISTENCE
   开启时跨轮拒绝复用（需求未变也要全新规划）
2. Run Trace（任务书 §11）：正常出单 → travel_plan_run 汇总 span +
   专家 span 全 success + trace.metadata["travel_persistence"]
"""
import json
import sys

sys.path.insert(0, r"D:\Program Files\workplace\agent")

import backend.config.travel as T
import backend.travel.graph_builder as gb
from backend.travel.graph_state import new_travel_graph_input


def _reset_graph():
    gb._travel_graph = None  # 强制重建，让 checkpointer 配置生效


def run(message: str, session: str, tid: str) -> dict:
    return gb.get_travel_graph().invoke(
        new_travel_graph_input(message, session_id=session),
        config={"configurable": {"thread_id": tid}},
    )


def report(checks: dict) -> bool:
    print("  断言:", json.dumps(checks, ensure_ascii=False))
    return all(checks.values())


ok_all = True

print("=" * 72)
print("场景 1：持久化降级可见化（memory 后端 → degraded）")
print("=" * 72)
T.TRAVEL_CHECKPOINTER_ENABLED = True
T.TRAVEL_CHECKPOINTER_BACKEND = "memory"
_reset_graph()

tid1 = "t-p4-deg-fixed"
f1 = run("福州2天行程，2个人", "s-p4-deg", tid1)
ans1 = f1.get("final_answer") or ""
sup1 = f1.get("supervisor_decision") or {}
print("  persistence_status=%s supervisor.persistence_status=%s" % (
    f1.get("persistence_status"), sup1.get("persistence_status")))
ok_all &= report({
    "state.persistence_status=degraded": f1.get("persistence_status") == "degraded",
    "supervisor 携带 degraded": sup1.get("persistence_status") == "degraded",
    "行程单含降级披露（临时存储）": "临时存储" in ans1,
})
for line in ans1.splitlines():
    if "临时存储" in line:
        print("  answer> %s" % line.strip()[:90])

print()
print("  —— 第二轮（需求未变 + TRAVEL_REQUIRE_PERSISTENCE=true）→ 必须全新规划 ——")
T.TRAVEL_REQUIRE_PERSISTENCE = True
f2 = run("福州2天行程，2个人", "s-p4-deg", tid1)  # 同 thread、同需求
notes2 = f2.get("notes", [])
it2 = f2.get("itinerary") or {}
T.TRAVEL_REQUIRE_PERSISTENCE = False
hit = [n for n in notes2 if "本轮按全新规划处理" in n]
print("  notes 命中: %s" % (hit[0][:70] if hit else "（无）"))
ok_all &= report({
    "require_fresh 全新规划 note": bool(hit),
    "plan 重算发生": it2.get("plan_version") is not None,
})

print()
print("=" * 72)
print("场景 2：正常出单 → travel_plan_run 汇总 span + 专家 span + trace metadata")
print("=" * 72)
T.TRAVEL_CHECKPOINTER_ENABLED = False
_reset_graph()

from backend.observability.tracer import trace_collector

trace_collector.clear_for_test()
record = trace_collector.start("福州1天行程", session_id="s-p4-trace",
                               workflow_name="travel_e2e")
# 走编排层适配器（travel_graph_node），才能覆盖 _stamp_execution_tags
# 的 trace.metadata["travel_persistence"] 打标——直连域图会绕过它
from backend.orchestration.graph.travel_graph_node import travel_graph_node

update3 = travel_graph_node({
    "question": "福州1天行程，1个人",
    "session_id": "s-p4-trace",
    "travel_context": {},
})
ans3 = (update3 or {}).get("final_answer") or ""
trace_collector.clear_for_test()

spans = record.spans
plan_run = [s for s in spans if s.span_id == "travel_plan_run"]
experts = [s for s in spans if s.span_id.startswith("travel_expert_")]
lbs = [s for s in spans if s.span_id.startswith("travel_lbs_")]
metrics = plan_run[0].metrics if plan_run else {}
print("  plan_run span=%d experts=%d lbs=%d" % (
    len(plan_run), len(experts), len(lbs)))
print("  expert spans: %s" % ", ".join(
    "%s(%s)" % (s.span_id.replace("travel_expert_", ""), s.status)
    for s in experts))
print("  plan_run metrics: %s" % json.dumps(
    {k: metrics.get(k) for k in ("destination", "plan_version",
                                 "persistence_status", "errors")},
    ensure_ascii=False))
ok_all &= report({
    "travel_plan_run span 恰好 1 个": len(plan_run) == 1,
    "metrics.destination=福州": metrics.get("destination") == "福州",
    "metrics.plan_version>=1": (metrics.get("plan_version") or 0) >= 1,
    "metrics.persistence_status=disabled": metrics.get("persistence_status") == "disabled",
    "metrics.errors=0": metrics.get("errors") == 0,
    "专家 span >=3": len(experts) >= 3,
    "专家 span 全 success": bool(experts) and all(
        s.status == "success" for s in experts),
    "trace.metadata 携带 travel_persistence":
        record.metadata.get("travel_persistence") == "disabled",
    "普通行程不提临时存储": "临时存储" not in ans3,
})

# 恢复默认配置，不污染同进程其他用途
T.TRAVEL_CHECKPOINTER_ENABLED = False
T.TRAVEL_CHECKPOINTER_BACKEND = "postgres"
T.TRAVEL_REQUIRE_PERSISTENCE = False
_reset_graph()

print()
print("=" * 72)
print("E2E RESULT: %s" % ("ALL PASS" if ok_all else "FAILED"))
print("=" * 72)
sys.exit(0 if ok_all else 1)
