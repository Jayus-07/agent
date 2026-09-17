# -*- coding: utf-8 -*-
"""Phase 5 校准脚本（临时，不进仓库）：全量跑 travel 数据集，
打印每条 case 期望 vs 实际，供校准 cases.jsonl 的 expected 字段。"""
import json
import sys

sys.path.insert(0, r"D:\Program Files\workplace\agent")

from backend.evaluation.dataset.loader import load_dataset
from backend.evaluation.runners.travel import _run_travel

cases = load_dataset("travel")
print("loaded %d cases" % len(cases))
results = _run_travel(cases)

npass = 0
for r in results:
    mark = "PASS" if r.status == "pass" else ("ERROR" if r.status == "error" else "FAIL")
    if r.status == "pass":
        npass += 1
    print("\n[%s] %s %sms" % (mark, r.case_id, r.duration_ms))
    if r.status != "pass":
        print("  reasons: %s" % r.error_msg)
        turns = (r.actual or {}).get("turns", [])
        for i, t in enumerate(turns, 1):
            print("  turn%d actual: status=%s dest=%s days=%s party=%s pace=%s v%s←%s repair=%s dr=%s err_v=%s conf=%s clarify=%s" % (
                i, t.get("status"), t.get("destination"), t.get("days"),
                t.get("party_size"), t.get("pace"), t.get("plan_version"),
                t.get("parent_plan_version"), t.get("repair_rounds"),
                t.get("decision_required"), t.get("error_violations"),
                t.get("confidence"), t.get("clarification")))
            print("    answer> %s" % (t.get("final_answer") or "")[:150].replace("\n", " ⏎ "))
            print("    notes> %s" % " | ".join(n[:40] for n in t.get("notes", [])[:4]))

print("\n==== %d/%d pass ====" % (npass, len(results)))
