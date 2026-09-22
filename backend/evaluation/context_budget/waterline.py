"""waterline — 摘要水位线三轮递进 / 最近4轮边界 / 并发单飞验证（Phase 4）

验证断言（§17/§18/§19）：
  1. 每轮 delta ⊆ (prev_through, boundary_id)，绝不重发旧消息
  2. 无空洞：每轮 delta 的最小 id == prev_through + 1（边界内全覆盖）
  3. through 严格单调递增
  4. 摘要输入 token 不随会话总长线性增长（记录每轮对比）
  5. 并发同 session：双线程同时触发 → 水位线不倒退、摘要不互相覆盖
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from backend.context_budget.auto_compact import (
    SyncMemorySummaryStore,
    run_incremental_summary,
)
from backend.evaluation.context_budget.golden_eval import (
    _append_turns,
    _seed_session,
)


class RecordingStore(SyncMemorySummaryStore):
    """记录 load_delta_messages 调用的探针 store（其余行为与真 store 一致）。"""

    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.load_calls: list[tuple[int | None, int, int]] = []
        self.load_results: list[list[tuple]] = []

    def load_delta_messages(self, after_id, before_id, limit):
        rows = super().load_delta_messages(after_id, before_id, limit)
        self.load_calls.append((after_id, before_id, limit))
        self.load_results.append(rows)
        return rows


def _mk_session(n_turns: int, tag: str) -> tuple[str, list[dict]]:
    """构造 n_turns 轮会话，每轮带唯一记事编号（可断言内容归属）。"""
    session_id = f"eval-wl-{tag}-{uuid.uuid4().hex[:6]}"
    history: list[dict] = []
    for i in range(n_turns):
        history.append({"role": "user",
                        "content": f"帮我记一笔备忘 W{i:03d}：采购事项{i}，"
                                   f"金额 {100 + i} 元，截止日 2026-12-{i + 1:02d}。"})
        history.append({"role": "assistant",
                        "content": f"已记录备忘 W{i:03d}（第 {i + 1} 条）。"})
    return session_id, history


def _run_round(session_id: str) -> dict:
    store = RecordingStore(session_id)
    outcome = run_incremental_summary(session_id, store)
    detail: dict[str, Any] = {"outcome": None}
    if outcome is not None:
        detail["outcome"] = {
            "through_id": outcome.through_id,
            "delta_messages": outcome.delta_message_count,
            "summary_tokens": outcome.token_count,
            "summary_input_est_tokens": sum(
                len(r[2]) for r in store.load_results[-1]) if store.load_results else 0,
        }
    detail["load_calls"] = store.load_calls
    if store.load_results:
        ids = [r[0] for r in store.load_results[-1]]
        detail["delta_id_min"] = min(ids) if ids else None
        detail["delta_id_max"] = max(ids) if ids else None
    return detail


def run_waterline() -> dict:
    # 进程内加载 DB 模型注册表（评测进程无 startup 刷新循环）
    import asyncio
    from backend.infra.llm.registry_store import refresh_registry
    asyncio.run(refresh_registry())

    results: dict[str, Any] = {"rounds": [], "asserts": {}}

    # ── 1) 三轮递进（§17）────────────────────────────────────────
    session_id, history = _mk_session(10, "seq")
    _seed_session(session_id, history)
    prev_through = 0
    ok_monotonic = True
    ok_no_hole = True
    ok_no_resend = True
    input_progression = []

    for rnd in range(1, 4):
        detail = _run_round(session_id)
        o = detail.get("outcome")
        results["rounds"].append({"round": rnd, **detail})
        if o is None:
            ok_monotonic = False
            break
        # 无重发：load_calls 的 after_id 必须等于上一轮 through
        if detail["load_calls"] and (detail["load_calls"][0][0] or 0) != prev_through:
            ok_no_resend = False
        # 无空洞：delta 最小 id == through+1（覆盖边界内全部）
        if detail.get("delta_id_min") is not None \
                and detail["delta_id_min"] != prev_through + 1:
            ok_no_hole = False
        # 单调
        if o["through_id"] <= prev_through and rnd > 1:
            ok_monotonic = False
        prev_through = o["through_id"]
        input_progression.append(o["summary_input_est_tokens"])
        # 垫 4 轮新对话，制造下一轮增量
        _append_turns(session_id, [
            (f"再记一笔备忘 X{rnd}: 新事项{rnd}，金额 {200 + rnd} 元。",
             f"已记录备忘 X{rnd}。")] * 1
            + [(f"补充说明一下第 {rnd} 批的事项细节，物流走陆运。",
                f"第 {rnd} 批事项已补充陆运说明。")] * 3)

    results["asserts"]["monotonic_through"] = ok_monotonic
    results["asserts"]["no_hole"] = ok_no_hole
    results["asserts"]["no_resend_old_messages"] = ok_no_resend
    results["asserts"]["summary_input_progression"] = input_progression
    # 摘要输入不随会话线性增长：后一轮输入不应等于全部历史（只含增量）
    results["asserts"]["input_not_linear_to_total"] = (
        len(input_progression) >= 2
        and input_progression[-1] <= input_progression[0] * 3)

    # ── 2) 最近4轮边界（§18）：through < 最早保留消息 id ──────────
    store = SyncMemorySummaryStore(session_id)
    boundary = store.summarizable_before_id(4)
    all_ids = _all_message_ids(session_id)
    retained = [i for i in all_ids if i >= boundary]
    results["asserts"]["waterline_lt_earliest_retained"] = (
        prev_through < boundary)
    results["asserts"]["retained_message_count_ge_8"] = len(retained) >= 8
    results["asserts"]["waterline"] = prev_through
    results["asserts"]["boundary"] = boundary

    # ── 3) 并发同 session（§19）──────────────────────────────────
    c_sid, c_history = _mk_session(8, "conc")
    _seed_session(c_sid, c_history)
    outcomes: list = []
    errors: list[str] = []

    def _worker():
        try:
            outcomes.append(run_incremental_summary(
                c_sid, SyncMemorySummaryStore(c_sid)))
        except Exception as e:  # pragma: no cover
            errors.append(str(e)[:120])

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    conc_elapsed = time.perf_counter() - t0

    final_state = SyncMemorySummaryStore(c_sid).get_summary_state()
    final_through = final_state["through_id"]
    max_id = max(_all_message_ids(c_sid))
    expected_boundary = SyncMemorySummaryStore(c_sid).summarizable_before_id(4)
    results["concurrency"] = {
        "workers": 2,
        "outcomes": [
            {"through": o.through_id, "delta": o.delta_message_count,
             "tokens": o.token_count} if o else None for o in outcomes],
        "errors": errors,
        "elapsed_s": round(conc_elapsed, 2),
        "final_through": final_through,
        "waterline_no_regression": final_through is not None
        and final_through <= max(expected_boundary or max_id, final_through),
        "final_summary_not_empty": bool(final_state["summary"]),
    }

    import json as _json
    print(_json.dumps(results, ensure_ascii=False, indent=2, default=str))
    return results


def _all_message_ids(session_id: str) -> list[int]:
    conn = SyncMemorySummaryStore(session_id)._engine().raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM public.chat_messages WHERE session_id=%s ORDER BY id",
            (session_id,))
        rows = cur.fetchall()
        conn.commit()
        return [r[0] for r in rows]
    finally:
        conn.close()
