# -*- coding: utf-8 -*-
"""Phase 3 e2e 真实样例（临时脚本，不进仓库）。

三场景：
1. 必去闭馆（福建博物院周一闭馆）→ needs_user_decision + 行程单「需要你决定」
2. 预算修复链回归（低预算 → repair → v2←v1 + degraded）
3. 普通行程 → ready + supervisor_decision.action 命名落键
"""
import json
import sys

sys.path.insert(0, r"D:\Program Files\workplace\agent")

from backend.travel.graph_builder import get_travel_graph
from backend.travel.graph_state import new_travel_graph_input


def run(message: str, tag: str) -> dict:
    from uuid import uuid4
    tid = f"t-p3-{tag}-{uuid4().hex[:8]}"
    final = get_travel_graph().invoke(
        new_travel_graph_input(message, session_id=f"s-p3-{tag}"),
        config={"configurable": {"thread_id": tid}},
    )
    return final


def show(final: dict) -> None:
    it = final.get("itinerary") or {}
    val = final.get("validation") or {}
    sup = final.get("supervisor_decision") or {}
    print("  plan_version=%s parent=%s brief_version=%s status=%s repair_rounds=%s" % (
        it.get("plan_version"), it.get("parent_plan_version"),
        it.get("brief_version"), it.get("status"), final.get("repair_rounds")))
    print("  change_reason=%s changed_fields=%s" % (
        it.get("change_reason"), it.get("changed_fields")))
    print("  supervisor: stage=%s action=%s" % (sup.get("stage"), sup.get("action")))
    for v in val.get("violations", []):
        print("  violation: %s [%s] %s" % (v["code"], v["level"], v["message"][:60]))
    ans = final.get("final_answer") or ""
    for line in ans.splitlines():
        if ("需要你决定" in line or "行程 v" in line or "修复" in line
                or "降级" in line or "无法自动" in line):
            print("  answer> %s" % line.strip()[:100])


print("=" * 72)
print("场景 1：必去闭馆 → needs_user_decision（周一 2026-09-21 福建博物院）")
print("=" * 72)
f1 = run("9月21日福州一日游，1个人，必去福建博物院", "closed")
show(f1)
it1 = f1.get("itinerary") or {}
val1 = f1.get("validation") or {}
viol1 = val1.get("violations", [])
dr = [v for v in viol1 if v["level"] == "decision_required"]
errs1 = [v for v in viol1 if v["level"] == "error"]
ans1 = f1.get("final_answer") or ""
checks1 = {
    "status=needs_user_decision": it1.get("status") == "needs_user_decision",
    "有 decision_required 违反": bool(dr),
    "无 error 违反": not errs1,
    "行程单含「需要你决定」": "需要你决定" in ans1,
    "supervisor action=finish_report": (f1.get("supervisor_decision") or {}).get("action") == "finish_report",
}
print("  断言:", json.dumps(checks1, ensure_ascii=False))

print()
print("=" * 72)
print("场景 2：预算修复链回归（低预算 → repair → degraded + v2←v1）")
print("=" * 72)
f2 = run("厦门2天行程，2个人，想把鼓浪屿、胡里山炮台、园林植物园都逛一遍，总预算250元", "budget")
show(f2)
it2 = f2.get("itinerary") or {}
viol2 = (f2.get("validation") or {}).get("violations", [])
errs2 = [v for v in viol2 if v["level"] == "error"]
repaired = f2.get("repair_rounds", 0) >= 1 and (it2.get("plan_version") or 0) >= 2
print("  断言: repair_rounds=%s plan_version=%s parent=%s status=%s errors=%d" % (
    f2.get("repair_rounds"), it2.get("plan_version"),
    it2.get("parent_plan_version"), it2.get("status"), len(errs2)))
print("  修复链形态: %s" % ("v2←v1 修复链成立" if repaired else "未触发修复（观察误差是否为空即可）"))

print()
print("=" * 72)
print("场景 3：普通行程 → ready + action 命名链")
print("=" * 72)
f3 = run("杭州2天行程，1个人，想看古迹", "plain")
show(f3)
it3 = f3.get("itinerary") or {}
viol3 = (f3.get("validation") or {}).get("violations", [])
errs3 = [v for v in viol3 if v["level"] == "error"]
sup3 = f3.get("supervisor_decision") or {}
checks3 = {
    "status=ready": it3.get("status") == "ready",
    "plan_version=1": it3.get("plan_version") == 1,
    "无 error": not errs3,
    "最后一跳 action=finish_report": sup3.get("action") == "finish_report",
    "版本脚注含「行程 v1」": "行程 v1" in (f3.get("final_answer") or ""),
}
print("  断言:", json.dumps(checks3, ensure_ascii=False))

ok1 = all(checks1.values())
ok3 = all(checks3.values())
print()
print("RESULT: 场景1=%s 场景3=%s（场景2 观察预算修复链形态）" % (
    "PASS" if ok1 else "FAIL", "PASS" if ok3 else "FAIL"))
