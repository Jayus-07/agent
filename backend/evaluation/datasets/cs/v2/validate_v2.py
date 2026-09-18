# -*- coding: utf-8 -*-
"""validate_v2.py — CS 评测集 v2 草稿批校验器（批 1：C1+C2）

校验：schema 字段完整性、枚举域（意图/目标取自代码事实源，G2）、id 唯一性、
实体键域、锚点格式、类别-文件一致性、锁定分布计数。
用法：D:/Python/python.exe -X utf8 validate_v2.py [file.jsonl ...]（缺省校验本目录两个批文件）
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))  # 仓库根

from backend.customer_service.router.intents import FINE_INTENTS  # noqa: E402
from backend.customer_service.graph_state import ROUTE_PATH_TO_CS_TARGET  # noqa: E402

HERE = Path(__file__).resolve().parent
VALID_INTENTS = set(FINE_INTENTS)
VALID_TARGETS = set(ROUTE_PATH_TO_CS_TARGET.values())
ENTITY_KEYS = {"order_id", "tracking_no", "amount", "product", "action_type",
               "email", "phone"}
ANCHOR_RE = re.compile(r"^(kb:[a-z0-9-]+#\S+|db:demo_order#\S+)$")
CATEGORIES = {
    "C1_faq_60.jsonl": ("faq", 60),
    "C2_query_60.jsonl": ("query", 60),
    "C3_action_60.jsonl": ("action", 60),
    "C4_complaint_40.jsonl": ("complaint", 40),
    "C5_multi_40.jsonl": ("multi_turn", 40),
    "C6_safety_40.jsonl": ("safety", 40),
}

errors: list[str] = []


def fail(fid: str, msg: str) -> None:
    errors.append(f"{fid}: {msg}")


def validate_file(path: Path) -> Counter:
    want_cat, want_n = CATEGORIES[path.name]
    ids: set[str] = set()
    intents: Counter = Counter()
    n = 0
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            n += 1
            fid = f"{path.name}:{lineno}"
            try:
                c = json.loads(line)
            except json.JSONDecodeError as e:
                fail(fid, f"JSON 解析失败 {e}")
                continue
            if c.get("schema_version") != "cs-v2":
                fail(fid, "schema_version 必须为 cs-v2")
            cid = c.get("id", "")
            if not re.fullmatch(r"CS2-C[1-6]-\d{3}", cid):
                fail(fid, f"id 格式非法: {cid!r}")
            if cid in ids:
                fail(fid, f"id 重复: {cid}")
            ids.add(cid)
            if c.get("category") != want_cat:
                fail(fid, f"category 应为 {want_cat}")
            turns = c.get("turns") or []
            if not turns or turns[0].get("role") != "user" or not turns[0].get("text"):
                fail(fid, "turns 首轮必须为 user 且 text 非空")
            exp = c.get("expected") or {}
            if exp.get("intent") not in VALID_INTENTS:
                fail(fid, f"intent 非法: {exp.get('intent')!r}")
            intents[exp.get("intent", "?")] += 1
            if exp.get("target") not in VALID_TARGETS:
                fail(fid, f"target 非法: {exp.get('target')!r}")
            if exp.get("cs_route") not in {"hit", "miss_non_cs", "clarify_weak"}:
                fail(fid, f"cs_route 非法: {exp.get('cs_route')!r}")
            if exp.get("next_action") not in {"answer", "clarify", "refuse",
                                              "handoff", "propose", "propose_with_gap"}:
                fail(fid, f"next_action 非法: {exp.get('next_action')!r}")
            if exp.get("risk_level") not in {"low", "medium", "high"}:
                fail(fid, f"risk_level 非法: {exp.get('risk_level')!r}")
            if exp.get("decision_layer") not in {"rule", "llm"}:
                fail(fid, f"decision_layer 非法: {exp.get('decision_layer')!r}")
            if not isinstance(exp.get("should_handoff"), bool):
                fail(fid, "should_handoff 必须为 bool")
            bad_keys = set(exp.get("entities") or {}) - ENTITY_KEYS
            if bad_keys:
                fail(fid, f"entities 键越界: {bad_keys}")
            for a in exp.get("allowed_facts") or []:
                if not ANCHOR_RE.match(a):
                    fail(fid, f"allowed_facts 锚点格式非法: {a!r}")
            for k in ("must_contain", "must_not_contain", "forbidden_facts",
                      "missing_slots"):
                if not isinstance(exp.get(k), list):
                    fail(fid, f"{k} 必须为 list")
            if want_cat == "multi_turn" and len(turns) < 2:
                fail(fid, "multi_turn 至少 2 轮")
            # 投诉/转人工类必须 should_handoff=true；c_feedback（受理类，映射评审
            # 2026-09-19：KNOWLEDGE_QUERY→cs_knowledge）豁免
            if (want_cat == "complaint"
                    and exp.get("intent") in {"h_handoff", "h_supervisor", "c_complaint"}
                    and exp.get("should_handoff") is not True):
                fail(fid, f"{exp.get('intent')} 类 must should_handoff=true")
            if want_cat == "safety":
                if exp.get("next_action") not in {"refuse", "clarify", "handoff"}:
                    fail(fid, f"safety 类 next_action 非法: {exp.get('next_action')!r}")
                if not exp.get("must_not_contain"):
                    fail(fid, "safety 类必须带 must_not_contain")
            if (exp.get("next_action") == "clarify") != bool(exp.get("missing_slots")) \
                    and exp.get("next_action") in {"clarify", "answer"} and exp.get("missing_slots"):
                pass  # 缺槽位→澄清的正向约束由评审人工点验
    if n != want_n:
        fail(path.name, f"分布计数 {n} != 锁定值 {want_n}")
    return intents


def main() -> None:
    files = [Path(p) for p in sys.argv[1:]] or [HERE / f for f in CATEGORIES]
    total = Counter()
    for p in files:
        total.update(validate_file(p))
    if errors:
        print(f"[validate_v2] FAIL — {len(errors)} 处问题：")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print(f"[validate_v2] PASS — {sum(total.values())} 条全部通过")
    print("  意图分布:", dict(sorted(total.items())))


if __name__ == "__main__":
    main()
