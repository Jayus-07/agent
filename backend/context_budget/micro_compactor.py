"""context_budget.micro_compactor — L3 previous_outputs 压缩

作用对象是 step_results / previous_outputs（工具输出驻留地），不是聊天历史。

规则（2026-09-23 STOP C 起为 dependency-aware 版）：
  1. 总预算：全部 previous_outputs 不超过 PREVIOUS_OUTPUTS_MAX_TOKENS
  2. 保留优先级（确定性、零 LLM）：
     priorities 高者优先完整保留；缺省退化为 latest-first（插入序尾部视为最新）
  3. 降级条目按工具类型分型压缩（SQL / RAG / 业务动作保留结构化关键字段，
     其余走通用 preview），L1 已产生 preview 的条目只允许再收缩、
     不允许重新获取或拼回完整结果
"""

from __future__ import annotations

from typing import Any

from backend.context_budget.tool_guard import (
    _record_compaction,
    is_compacted_preview,
    serialize_for_count,
    truncate_text_to_tokens,
)

# 降级条目的 preview 上限（token）：比 L1 的 TOOL_PREVIEW_MAX_TOKENS 更紧，
# 旧结果只需要"知道有什么"而不需要完整内容
_PREVIEW_DEGRADE_TOKENS = 128

# 分型压缩白名单键（§十三）：确定性 key picking，不做任何语义理解
_SQL_KEYS = (
    "query", "sql", "sql_text", "columns", "row_count", "rows_count",
    "total", "aggregates", "agg", "sample_rows", "rows", "ids", "important_ids",
)
_RAG_KEYS = (
    "doc_ids", "doc_id", "chunk_ids", "chunk_id", "source_file",
    "source_files", "sources", "rerank_score", "score", "citations",
    "citation", "evidence_snippet", "evidence",
)
_BIZ_KEYS = (
    "entity_id", "order_id", "user_id", "operation", "action", "status",
    "confirmation_id", "result",
)
_BIZ_CAP_PREFIXES = ("order.", "customer.", "inventory.", "finance.",
                     "approval.")

# 依赖感知打分权重（规格 §十二 推荐优先级的确定性近似）
_SCORE_DIRECT_DEP = 1.0        # 当前步骤直接前驱（注入集合全部满足）
_SCORE_ON_FINAL_PATH = 2.0     # 可达终局步骤（report/final answer）
_SCORE_DECISION_CAP = 1.0      # 决策关键能力（sql/business 分析）
_SCORE_RAG_CAP = 0.5           # 知识证据能力


def _enabled() -> bool:
    try:
        from backend.config import CONTEXT_BUDGET_ENABLED
        return bool(CONTEXT_BUDGET_ENABLED)
    except Exception:  # pragma: no cover
        return True


def _budget() -> int:
    from backend.config import PREVIOUS_OUTPUTS_MAX_TOKENS
    return PREVIOUS_OUTPUTS_MAX_TOKENS


def _count_output_tokens(output: Any) -> int:
    from backend.memory.token_budget import count_tokens
    return count_tokens(serialize_for_count(output))


def _dump_output(output: Any) -> Any:
    """pydantic 输出（SQLResult/BusinessInsight 等）转 dict，其余原样。"""
    if hasattr(output, "model_dump"):
        try:
            return output.model_dump()
        except Exception:
            return output
    return output


def _cap_str(value: Any, n: int = 48) -> str:
    s = str(value)
    return s if len(s) <= n else s[:n] + "…"


def _pick_typed(output: Any, keys: tuple[str, ...]) -> dict | None:
    """从结构化输出里白名单挑键（截断字符串/列表，保证降级条目有界）。"""
    out = _dump_output(output)
    if not isinstance(out, dict):
        return None
    picked: dict[str, Any] = {}
    for k in keys:
        v = out.get(k)
        if v is None or v == "" or v == [] or v == {}:
            continue
        if isinstance(v, str):
            v = _cap_str(v)
        elif isinstance(v, list):
            v = [
                _cap_str(x, 32) if not isinstance(x, dict)
                else {kk: _cap_str(vv, 32) for kk, vv in list(x.items())[:6]}
                for x in v[:5]
            ]
        elif isinstance(v, dict):
            v = {kk: _cap_str(vv, 32) for kk, vv in list(v.items())[:6]}
        picked[k] = v
    return picked or None


def _degrade_entry(
    dep_id: str,
    output: Any,
    meta: dict | None,
    *,
    preview_tokens: int = _PREVIEW_DEGRADE_TOKENS,
) -> dict:
    """把一条输出降级为摘要结构（保留可追溯的最小信息，不无声丢失）。"""
    from backend.memory.token_budget import count_tokens

    already_compacted = is_compacted_preview(output)
    if already_compacted:
        # L1 preview：不拼回完整结果，只允许 preview 再收缩
        preview = truncate_text_to_tokens(
            str(output.get("preview", "")), preview_tokens)
        original_tokens = int(output.get("original_tokens", 0)) or None
    else:
        text = serialize_for_count(output)
        preview = truncate_text_to_tokens(text, preview_tokens)
        original_tokens = count_tokens(text)

    entry: dict[str, Any] = {
        "step_id": (meta or {}).get("step_id", dep_id),
        "tool": (meta or {}).get("tool", ""),
        "status": (meta or {}).get("status", "success"),
        "preview": preview,
        "compacted": True,
    }
    if already_compacted:
        # 保留 L1 preview 标记：L3 输出再次进入 L3/L1 判定时，
        # is_compacted_preview 仍识别为 preview（只收缩、不重载全文）
        entry["context_compacted"] = True
        entry["type"] = "tool_result_preview"
    if original_tokens:
        entry["original_tokens"] = original_tokens
    return entry


def _degrade_output(
    dep_id: str,
    output: Any,
    meta: dict | None,
) -> dict:
    """分型降级入口（§十三）：按能力前缀挑结构化关键字段，其余通用 preview。

    L1 preview 条目一律走通用收缩路径（绝不重新加载全文）。
    """
    if is_compacted_preview(output):
        return _degrade_entry(dep_id, output, meta)

    cap = ((meta or {}).get("tool") or "").strip()
    typed: dict | None = None
    if cap.startswith("sql."):
        typed = _pick_typed(output, _SQL_KEYS)
    elif cap.startswith(("rag.", "knowledge.")):
        typed = _pick_typed(output, _RAG_KEYS)
    elif cap.startswith(_BIZ_CAP_PREFIXES):
        typed = _pick_typed(output, _BIZ_KEYS)

    if typed:
        entry: dict[str, Any] = {
            "step_id": (meta or {}).get("step_id", dep_id),
            "tool": cap,
            "status": (meta or {}).get("status", "success"),
            "compacted": True,
        }
        entry.update(typed)
        return entry
    return _degrade_entry(dep_id, output, meta)


def compute_dependency_ranks(
    edges: dict[str, list[str]],
    current_step_id: str,
    step_results: dict[str, dict],
) -> dict[str, float]:
    """从执行 DAG 计算各前驱步骤的保留优先级（确定性，零 LLM）。

    分数构成（越高越优先完整保留）：
      - 直接前驱基分（注入集合天然满足）
      - 可达终局步骤（无人依赖它的步骤 ≈ 产出最终答案）+2
      - 决策关键能力（sql.*/business.*）+1；知识证据（rag.*）+0.5
      - 下游依赖数 ×0.5（被越多后续步骤消费越有价值）
    """
    dependents: dict[str, list[str]] = {}
    all_steps: set[str] = set()
    for step_id, deps in (edges or {}).items():
        all_steps.add(step_id)
        for dep in deps or []:
            all_steps.add(dep)
            dependents.setdefault(dep, []).append(step_id)

    def _reachable_from(step: str, seen: set[str] | None = None) -> set[str]:
        seen = seen or set()
        for nxt in dependents.get(step, []):
            if nxt not in seen:
                seen.add(nxt)
                _reachable_from(nxt, seen)
        return seen

    terminals = {s for s in all_steps if not dependents.get(s)}

    ranks: dict[str, float] = {}
    for dep in (edges or {}).get(current_step_id, []) or []:
        score = _SCORE_DIRECT_DEP
        reach = _reachable_from(dep)
        if reach & terminals:
            score += _SCORE_ON_FINAL_PATH
        score += 0.5 * len(reach)
        capability = str(
            (step_results or {}).get(dep, {}).get("capability") or "")
        if capability.startswith(("sql.", "business.")):
            score += _SCORE_DECISION_CAP
        elif capability.startswith(("rag.", "knowledge.")):
            score += _SCORE_RAG_CAP
        ranks[dep] = score
    return ranks


def compact_previous_outputs(
    previous_outputs: dict[str, Any] | None,
    *,
    meta: dict[str, dict] | None = None,
    priorities: dict[str, float] | None = None,
) -> dict[str, Any]:
    """L3 入口：对注入下一 Skill prompt 的 previous_outputs 做总预算压缩。

    meta: 可选的 {dep_id: {step_id, tool, status}} 元数据表（调用方从
    step_results 提取），降级条目用它保留可追溯信息；缺省以 dep_id 兜底。
    priorities: {dep_id: score} 保留优先级（compute_dependency_ranks 产出）；
    缺省退化为 latest-first（插入序尾部视为最新）。

    返回处理后的 dict（可能原样返回——未超预算时零改动，零拷贝开销）。
    """
    if not previous_outputs or not _enabled():
        return previous_outputs or {}

    budget = _budget()
    if budget <= 0:
        return previous_outputs

    meta = meta or {}

    # 无输出超预算 → 原样返回（快路径：大多数轮次走这里）
    entries = [
        (dep_id, output, _count_output_tokens(output))
        for dep_id, output in previous_outputs.items()
    ]
    total = sum(t for _, _, t in entries)
    if total <= budget:
        return previous_outputs

    before_tokens = total
    from backend.memory.token_budget import count_tokens

    # 保留顺序：priority 高在前；同级时插入序尾部（更新）在前。
    # 一旦预算耗尽，剩余条目按该顺序的**逆序**降级（最不重要先降级）。
    index_of = {dep_id: i for i, (dep_id, _, _) in enumerate(entries)}
    order = sorted(
        range(len(entries)),
        key=lambda i: (-float((priorities or {}).get(entries[i][0], 0.0)),
                       -i),
    )

    compacted: dict[str, Any] = {}
    used = 0
    overflow = False
    for idx in order:
        dep_id, output, tokens = entries[idx]
        if not overflow and used + tokens <= budget:
            used += tokens
            compacted[dep_id] = output
        else:
            overflow = True
            entry = _degrade_output(dep_id, output, meta.get(dep_id))
            entry_tokens = count_tokens(serialize_for_count(entry))
            # 降级条目预算挤不下时收缩其 preview（下限 32，保证至少有摘要可读）
            if entry_tokens > max(0, budget - used) and _PREVIEW_DEGRADE_TOKENS > 32:
                entry = _degrade_entry(
                    dep_id, output, meta.get(dep_id), preview_tokens=32)
                entry_tokens = count_tokens(serialize_for_count(entry))
            compacted[dep_id] = entry
            used += entry_tokens
    # 恢复原插入序
    result = {dep_id: compacted[dep_id] for dep_id, _, _ in entries if dep_id in compacted}

    after_tokens = count_tokens(serialize_for_count(result))
    _record_compaction(
        level="L3", action="previous_outputs_compact",
        before_tokens=before_tokens, after_tokens=after_tokens,
    )
    from backend.context_budget.metrics import emit_context_event
    emit_context_event(
        level="L3", action="previous_outputs_compact",
        before_tokens=before_tokens, after_tokens=after_tokens,
    )
    return result
