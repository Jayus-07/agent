# -*- coding: utf-8 -*-
"""Phase 6 e2e 真实样例（临时脚本，不进仓库）。

User Decision interrupt 全链路（任务书 §13）：
1. interrupt 触发：必去闭馆 → 图暂停 + payload 结构化
2. resume keep → degraded 行程单（保留留档，不再询问）
3. resume drop → 移除 + 需求同步解除 + ready
4. 编排适配器两段式（pending_decision → resume_decision 通道）
"""
import json
import sys

sys.path.insert(0, r"D:\Program Files\workplace\agent")

import backend.config.travel as T
import backend.travel.graph_builder as gb
from backend.travel.graph_state import new_travel_graph_input

Q = "9月21日福州一日游，1个人，必去福建博物院"
ok_all = True


def report(checks: dict) -> None:
    global ok_all
    print("  断言:", json.dumps(checks, ensure_ascii=False))
    ok_all &= all(checks.values())


def setup():
    T.TRAVEL_USER_DECISION_INTERRUPT = True
    T.TRAVEL_CHECKPOINTER_ENABLED = True
    T.TRAVEL_CHECKPOINTER_BACKEND = "memory"
    gb._travel_graph = None


def teardown():
    T.TRAVEL_USER_DECISION_INTERRUPT = False
    T.TRAVEL_CHECKPOINTER_ENABLED = False
    gb._travel_graph = None


def payload_of(final: dict) -> dict:
    for it in final.get("__interrupt__") or []:
        v = getattr(it, "value", None)
        if isinstance(v, dict) and v.get("items"):
            return v
    return {}


setup()
graph = gb.get_travel_graph()

print("=" * 72)
print("场景 1：interrupt 触发（必去闭馆 → 图暂停）")
print("=" * 72)
tid = "t-p6-e2e-keep"
f1 = graph.invoke(new_travel_graph_input(Q, session_id="s-p6-e2e"),
                  config={"configurable": {"thread_id": tid}})
p = payload_of(f1)
print("  items: %s" % [i["poi_name"] for i in p.get("items", [])])
report({"图已暂停": bool(p),
        "payload 点名福建博物院": any(
            i["poi_name"] == "福建博物院" for i in p.get("items", []))})

print()
print("场景 2：resume keep → degraded 交付（风险自担）")
print("=" * 72)
from langgraph.types import Command
f2 = graph.invoke(Command(resume={"action": "keep"}),
                  config={"configurable": {"thread_id": tid}})
it2 = f2.get("itinerary") or {}
ans2 = f2.get("final_answer") or ""
titles2 = [i["title"] for d in it2.get("days", []) for i in d.get("items", [])]
print("  status=%s 置信度=%s answer尾部=%s" % (
    it2.get("status"), it2.get("confidence"), ans2.strip().splitlines()[-1][:70]))
report({"status=degraded": it2.get("status") == "degraded",
        "行程仍含福建博物院": "福建博物院" in titles2,
        "出单完成（行程 v）": "行程 v" in ans2,
        "notes 留档保留决定": any("保留" in n for n in f2.get("notes", []))})

print()
print("场景 3：resume drop → 移除 + 需求解除 + ready")
print("=" * 72)
tid2 = "t-p6-e2e-drop"
graph.invoke(new_travel_graph_input(Q, session_id="s-p6-e2e"),
             config={"configurable": {"thread_id": tid2}})
f3 = graph.invoke(Command(resume={"drop": ["福建博物院"]}),
                  config={"configurable": {"thread_id": tid2}})
it3 = f3.get("itinerary") or {}
ans3 = f3.get("final_answer") or ""
titles3 = [i["title"] for d in it3.get("days", []) for i in d.get("items", [])]
report({"行程已移除福建博物院": "福建博物院" not in titles3,
        "must_go 同步解除": "福建博物院" not in (f3.get("brief") or {}).get("must_go", []),
        "status=ready": it3.get("status") == "ready",
        "出单完成": "行程 v" in ans3})

print()
print("场景 4：编排适配器两段式（pending_decision → resume_decision）")
print("=" * 72)
from backend.orchestration.graph.travel_graph_node import travel_graph_node
cid = "t-p6-e2e-adapter"
u1 = travel_graph_node({"question": Q, "session_id": "s-p6-e2e",
                        "travel_context": {"conversation_id": cid}})
pending = (u1.get("travel_context") or {}).get("pending_decision") or {}
u2 = travel_graph_node({"question": "", "session_id": "s-p6-e2e",
                        "travel_context": {"conversation_id": cid,
                                           "resume_decision": {"action": "keep"}}})
report({"第一段透传请决定": "需要你决定" in (u1.get("final_answer") or ""),
        "pending_decision 结构化": any(
            i["poi_name"] == "福建博物院" for i in pending.get("items", [])),
        "第二段 resume 出单": "行程 v" in (u2.get("final_answer") or "")})

teardown()
print()
print("=" * 72)
print("E2E RESULT: %s" % ("ALL PASS" if ok_all else "FAILED"))
print("=" * 72)
sys.exit(0 if ok_all else 1)
