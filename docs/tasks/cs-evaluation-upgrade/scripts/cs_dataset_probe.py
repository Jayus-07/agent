# -*- coding: utf-8 -*-
"""CS 评测集离线回放探针（cs-evaluation-upgrade 评估产物，P1 固化入 CI 的基础）。

回放 320 条锁版断言（decision_layer=rule 口径，零 LLM）：
  query -> CSRouter.route()（完整生产入口：DomainDetector→Coarse→Fine）
        -> intent / route_path / risk_level
  对照 expected.{intent,target,risk_level}；target 经 CSRoutePath→cs_* 值域映射。

跑法（worktree 根、主仓库解释器）：
  "D:/Program Files/workplace/agent/.venv/Scripts/python.exe" \
      docs/tasks/cs-evaluation-upgrade/scripts/cs_dataset_probe.py

2026-11-01 基线：intent 40.6% / target 52.8% / risk 63.4%（逐类目见 TASK §1.1）。
2026-11-02（P1 核对修正）：TARGET_MAP 的 human_handoff 由 cs_human 改为 cs_handoff
（数据集 target 值域实为 cs_handoff，17 条 handoff 用例此前被系统性误判 miss）。
2026-11-02（P2 数据集升 v2.1）：修订 faq k_policy/k_faq 标注口径（规则层 k_policy=
显式政策词命中、k_faq=设计内兜底）+ 22 条 intent→target 映射矛盾，依据见 manifest
notes。2026-11-02（P3 词表扩圈 + 收编升 v2.2 共 324 条）：coarse/fine 词表覆盖
自然表达（ ACCOUNT 细分、t_logistics/t_delivery_estimate、余额/取消订单等），
4 条候选收编。v2.2 全量回放 intent 71.3% / target 76.5% / risk 69.7%（324 口径）。
门禁（backend/tests/customer_service/test_cs_decision_replay.py）口径：
multi_turn 整类与 safety 的 risk 维度离线 skip，基线 intent 75.7%（215/284）/
target 80.3%（228/284）/ risk 79.9%（195/244），阈值防退化
intent≥0.74 / target≥0.79 / risk≥0.78。
注意：本脚本与门禁回放均需禁用 cs_router 路由缓存（共享 Redis TTL 300s 会
回放陈旧结果——门禁测试内置 _NoCache stub，手工跑本脚本时如遇数字异常先清缓存）。
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # scripts→任务→tasks→docs→仓库根
sys.path.insert(0, str(REPO))

from backend.customer_service.router.cs_router import get_cs_router

DS = REPO / "backend" / "evaluation" / "datasets" / "cs"
cases = [json.loads(l) for l in (DS / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
print(f"cases.jsonl (locked): {len(cases)}")

# v2.deprecated/ 生成中间分片归档与锁版的差异盘点（2026-11-02 定案改名）
v2_ids = set()
for f in sorted((DS / "v2.deprecated").glob("*.jsonl")):
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            v2_ids.add(json.loads(line)["id"])
locked_ids = {c["id"] for c in cases}
print(f"v2/ 分片: {len(v2_ids)} 条; 交集 {len(v2_ids & locked_ids)}, "
      f"仅分片 {len(v2_ids - locked_ids)}, 仅锁版 {len(locked_ids - v2_ids)}")

router = get_cs_router()
TARGET_MAP = {
    "knowledge_query": "cs_knowledge",
    "business_query": "cs_business_query",
    "business_action": "cs_business_action",
    "complaint_flow": "cs_complaint",
    "human_handoff": "cs_handoff",  # 2026-11-02 修正（原 cs_human，数据集值域为 cs_handoff）
}

by_cat = defaultdict(lambda: Counter())
fails = defaultdict(list)

for c in cases:
    exp = c.get("expected", {})
    cat = c.get("category", "v1")
    turns = c.get("turns") or []
    q = turns[0]["text"] if turns else c.get("question", "")

    try:
        r = router.route(q)
        pred_intent, pred_risk = r.intent, r.risk_level
        pred_target = TARGET_MAP.get(r.route_path.value, "")
    except Exception as e:  # noqa: BLE001 — 回放不中断，单条失败计入
        pred_intent, pred_risk, pred_target = f"<error:{type(e).__name__}>", "", ""

    exp_intent, exp_target, exp_risk = exp.get("intent"), exp.get("target"), exp.get("risk_level")
    stat = by_cat[cat]
    stat["n"] += 1
    ok_i = exp_intent is None or pred_intent == exp_intent
    ok_t = exp_target is None or pred_target == exp_target
    ok_r = exp_risk is None or pred_risk == exp_risk
    stat["intent_hit"] += ok_i; stat["target_hit"] += ok_t; stat["risk_hit"] += ok_r
    if not ok_i:
        fails[cat].append((c["id"], exp_intent, pred_intent, q[:36]))

print("\n=== 320 锁版 · 完整路由入口回放 ===")
tot = Counter()
for cat, s in sorted(by_cat.items()):
    n = s["n"]
    print(f"{cat:12s} n={n:3d} intent={s['intent_hit']/n*100:5.1f}% "
          f"target={s['target_hit']/n*100:5.1f}% risk={s['risk_hit']/n*100:5.1f}%")
    tot.update({"n": n, "i": s["intent_hit"], "t": s["target_hit"], "r": s["risk_hit"]})
print(f"\nTOTAL n={tot['n']} intent={tot['i']/tot['n']*100:.1f}% "
      f"target={tot['t']/tot['n']*100:.1f}% risk={tot['r']/tot['n']*100:.1f}%")

print("\n=== intent 失败样例（每类目前 4）===")
for cat, items in fails.items():
    for cid, e, g, q in items[:4]:
        print(f"  [{cat}] {cid}: exp={e} got={g} q={q}")
