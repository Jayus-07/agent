"""golden_eval — L5 事实保真 Golden Cases 双轨评测（Phase 4）

流程（每例）：
  1. 真实建 session + 落库 chat_messages（历史含噪声）
  2. Baseline：原始历史直接问答（足够窗口，不经 L5）
  3. After-L5：真实 run_incremental_summary（真 LLM/真 DB 水位线）
     → fold_rebuild 重建投影 → 摘要+最近4轮 问答
  4. 计分：must_contain（标识符/数字精确匹配）+ must_not_contain（否定/纠正）
     + ProtectedFact Recall（summary 是否保住抽取事实）
     + patched 分层（LLM 原生保留 vs deterministic patch 补回）

指标定义：
  Fact Retention Rate      = 双轨答案中保住的关键事实 / 应保留关键事实
  Protected Fact Recall    = 摘要最终保留的 protected facts / 抽取出的 protected facts
  Patched Ratio            = patched / protected_facts_total（安全网占比，非越低越好）
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

DATASET = Path(__file__).resolve().parents[1] / "datasets" / "context_budget_golden.json"

_SYSTEM = "你是一个严谨的电商业务助手，只根据对话历史中的信息回答，不要编造。"

# 填充轮：无业务事实的中性对话，垫高轮数使真实水位线边界（最近4轮）落在填充区，
# 从而全部事实历史都进入摘要范围（贴近真实长会话形态）
_FILLER = [("嗯，先这样，你有空再帮我看看。", "好的，我记着呢，您随时继续。")]


def _load_cases(limit: int = 0, category: str = "") -> list[dict]:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = data["cases"]
    if category:
        cases = [c for c in cases if c["category"] == category]
    if limit:
        cases = cases[:limit]
    return cases


def _normalize(text: str) -> str:
    return re.sub(r"[\s,，]", "", text or "").lower()


# ── 真实 DB 会话写入（agent_memory，raw_connection 归还池）────────────

def _seed_session(session_id: str, history: list[dict]) -> list[int]:
    """落库会话与历史消息，返回消息 id 列表（升序）。"""
    from backend.infra.db import get_memory_engine

    conn = get_memory_engine().raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.chat_sessions (session_id, user_id) "
            "VALUES (%s, %s) ON CONFLICT (session_id) DO NOTHING",
            (session_id, "p4-golden"),
        )
        ids: list[int] = []
        for m in history:
            cur.execute(
                "INSERT INTO public.chat_messages (session_id, role, content) "
                "VALUES (%s, %s, %s) RETURNING id",
                (session_id, m["role"], m["content"]),
            )
            ids.append(cur.fetchone()[0])
        conn.commit()
        return ids
    finally:
        conn.close()


def _append_turns(session_id: str, turns: list[tuple[str, str]]) -> list[int]:
    from backend.infra.db import get_memory_engine

    conn = get_memory_engine().raw_connection()
    try:
        cur = conn.cursor()
        ids: list[int] = []
        for u, a in turns:
            for role, content in (("user", u), ("assistant", a)):
                cur.execute(
                    "INSERT INTO public.chat_messages (session_id, role, content) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (session_id, role, content),
                )
                ids.append(cur.fetchone()[0])
        conn.commit()
        return ids
    finally:
        conn.close()


def _pad_to_boundary(history: list[dict]) -> list[dict]:
    """垫 4 个中性轮，让最近4轮保护边界落在填充区。"""
    padded = list(history)
    padded.extend({"role": r, "content": c}
                  for u, a in _FILLER * 4 for r, c in (("user", u), ("assistant", a)))
    return padded


def _invoke(messages: list) -> str:
    from backend.infra.llm import llm
    resp = llm.invoke(messages)
    return getattr(resp, "content", None) or str(resp)


def run_golden(limit: int = 0, category: str = "") -> dict:
    # 进程内加载 DB 模型注册表（评测进程无 startup 刷新循环）
    import asyncio
    from backend.infra.llm.registry_store import refresh_registry
    asyncio.run(refresh_registry())

    from backend.context_budget.auto_compact import (
        SyncMemorySummaryStore,
        extract_protected_facts,
        fold_rebuild,
        run_incremental_summary,
    )

    cases = _load_cases(limit, category)
    rows: list[dict] = []
    totals = {"protected": 0, "preserved_by_llm": 0, "patched": 0,
              "missed_final": 0}

    for case in cases:
        case_id = case["id"]
        session_id = f"eval-golden-{time.strftime('%Y%m%d')}-{case_id}-{uuid.uuid4().hex[:6]}"
        expect = case["expect"]

        history = _pad_to_boundary(case["history"])
        msg_ids = _seed_session(session_id, history)
        rows_for_facts = [
            (mid, m["content"]) for mid, m in zip(msg_ids, history)]

        # 1) 真实增量摘要（真 LLM + 真 DB 水位线）
        t0 = time.perf_counter()
        outcome = run_incremental_summary(
            session_id, SyncMemorySummaryStore(session_id))
        summary_latency = time.perf_counter() - t0

        history_msgs = [
            HumanMessage(content=m["content"]) if m["role"] == "user"
            else AIMessage(content=m["content"])
            for m in history
        ]
        question = case["question"]

        # 2) Baseline：原始历史（不经 L5）
        baseline_msgs = [SystemMessage(content=_SYSTEM)] + history_msgs \
            + [HumanMessage(content=question)]
        baseline_answer = _invoke(baseline_msgs)

        row: dict[str, Any] = {"id": case_id, "category": case["category"],
                               "session_id": session_id}
        if outcome is None:
            row.update({"summary_ok": False,
                        "error": "run_incremental_summary returned None"})
            rows.append(row)
            continue

        # 3) After-L5：摘要 + 最近4轮 原文
        compacted, replaced, _boundary = fold_rebuild(history_msgs, outcome.summary)
        compacted_msgs = [SystemMessage(content=_SYSTEM)] + compacted \
            + [HumanMessage(content=question)]
        compacted_answer = _invoke(compacted_msgs)

        # 4) 计分（确定性 substring，归一化空白/逗号/大小写）
        def check(answer: str, musts: list[str], forbids: list[str]) -> dict:
            norm = _normalize(answer)
            hit = [m for m in musts if _normalize(m) in norm]
            leak = [f for f in forbids if _normalize(f) in norm]
            return {"hit": hit, "miss": [m for m in musts if m not in hit],
                    "leak": leak}

        b_chk = check(baseline_answer, expect["must_contain"],
                      expect.get("must_not_contain", []))
        c_chk = check(compacted_answer, expect["must_contain"],
                      expect.get("must_not_contain", []))

        # ProtectedFact Recall（对摘要文本）
        facts = extract_protected_facts(rows_for_facts)
        fact_values = list({f.value for f in facts})
        if expect.get("protected"):
            fact_values += [p for p in expect["protected"]
                            if p not in fact_values]
        summary_norm = _normalize(outcome.summary)
        kept = [f for f in fact_values if _normalize(f) in summary_norm]
        # facts 值可能是捕获组子串（如 SKU 去前缀），摘要里保留完整串也算保留
        kept_alt = [f for f in fact_values if f not in kept and any(
            _normalize(f) in _normalize(v) or _normalize(v) in _normalize(f)
            for v in kept)]
        preserved = kept + kept_alt

        preserved_by_llm = outcome.protected_fact_count - outcome.patched_fact_count
        totals["protected"] += len(fact_values)
        totals["preserved_by_llm"] += preserved_by_llm
        totals["patched"] += outcome.patched_fact_count
        totals["missed_final"] += len(fact_values) - len(preserved)

        row.update({
            "summary_ok": True,
            "summary_tokens": outcome.token_count,
            "summary_latency_s": round(summary_latency, 2),
            "delta_messages": outcome.delta_message_count,
            "protected_facts_total": len(fact_values),
            "protected_facts_preserved_in_summary": len(preserved),
            "llm_native_preserved": preserved_by_llm,
            "patched": outcome.patched_fact_count,
            "baseline": {"answer_head": baseline_answer[:120],
                         **b_chk},
            "after_L5": {"answer_head": compacted_answer[:120], **c_chk},
            "fact_retention_baseline": (
                len(b_chk["hit"]) / len(expect["must_contain"])
                if expect["must_contain"] else None),
            "fact_retention_after_L5": (
                len(c_chk["hit"]) / len(expect["must_contain"])
                if expect["must_contain"] else None),
            "constraint_violation": bool(c_chk["leak"]),
            "summary_text": outcome.summary[:400],
        })
        rows.append(row)
        print(f"[{case_id}] retention_after_L5="
              f"{row['fact_retention_after_L5']} patched={outcome.patched_fact_count}"
              f"/{outcome.protected_fact_count} "
              f"summary_tokens={outcome.token_count}", flush=True)

    scored = [r for r in rows if r.get("summary_ok")]
    req_n = [len(_load_cases() and [c for c in [cases[i]]] and
              [1]) for i, _ in enumerate(cases)]  # placeholder，下方直接算

    report = {
        "mode": "golden",
        "cases_total": len(cases),
        "cases_scored": len(scored),
        "fact_retention_rate": (
            sum(r["fact_retention_after_L5"] for r in scored
                if r["fact_retention_after_L5"] is not None)
            / max(1, sum(1 for r in scored
                         if r["fact_retention_after_L5"] is not None))),
        "protected_fact_recall": (
            (totals["protected"] - totals["missed_final"])
            / totals["protected"]) if totals["protected"] else None,
        "patched_ratio": (
            totals["patched"] / totals["protected"]) if totals["protected"] else None,
        "protected_facts": totals,
        "constraint_violations": [r["id"] for r in scored
                                  if r["constraint_violation"]],
        "detail": rows,
    }
    print(json.dumps({k: v for k, v in report.items() if k != "detail"},
                     ensure_ascii=False, indent=2))
    return report
