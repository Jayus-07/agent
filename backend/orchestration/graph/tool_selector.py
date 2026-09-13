"""tool_selector.py — direct 模式的 FC 工具选择节点（tool RAG / 动态工具暴露）

三层路由（rule→vector→LLM）已把候选缩到 top-K；本节点把 K 个候选以
bind_tools 形式暴露给模型，由模型在候选内选一个并同步填参——即
"先路由缩候选，再给模型选"。取代 direct 模式"盲取 candidates[0] +
question 透传"的旧行为。

门控（ENABLE_FC_TOOL_SELECTION 且 route_mode=direct）：
  - fast set（sql.query/rag.search/business.analyze：参数平凡或由
    previous_outputs 自动注入）且 top1 ≥ 0.85 → 直通，零 LLM
    （保持原 direct 快路径，TTFT 不受影响）
  - 其余 capability 或置信灰区（0.6 ≤ score < 0.85）→ FC 选择

降级兜底（每一步失败都等价旧行为，最坏不多花时间）：
  - LLM 超时/异常/无 tool_calls（含「无匹配」回复、降级话术）→
    passthrough：candidates[0] + {"question": 原话}，即旧 direct 行为
  - 选择越界/参数校验失败 → 带反馈重试 1 次，仍失败 → passthrough
"""
from __future__ import annotations

import time

from backend.config import (
    ENABLE_FC_TOOL_SELECTION,
    TOOL_SELECTOR_LLM_TIMEOUT,
    TOOL_SELECTOR_LLM_MAX_TOKENS,
)
from backend.infra.llm import llm
from backend.infra.timeout import safe_call_with_timeout
from backend.orchestration.tool_registry import tool_registry
from backend.orchestration.tool_schema import (
    capabilities_to_tools,
    capability_to_function_name,
)
from backend.shared.logger import logger
from backend.skills.base import validate_params

# 参数平凡（只有 question）/自动注入（business.analyze 的 sql_result 走
# previous_outputs）的高频能力：路由高置信时直通，避免无意义的 LLM 调用
FAST_PATH_CAPS = {"sql.query", "rag.search", "business.analyze"}
FAST_PATH_SCORE = 0.85
# FC 候选上限：路由缩候选后通常 ≤3，防止极端情况下 prompt 膨胀
MAX_FC_CANDIDATES = 3

_SYSTEM_PROMPT = """你是电商运营平台的工具选择器。根据用户问题，从候选工具中选出最合适的一个，并从问题中抽取该工具需要的全部参数。

规则：
- 只能调用候选列表中的工具，禁止调用其他任何工具
- 参数值必须来自用户问题，禁止编造；问题中没有的信息不要填
- 若所有候选工具都不适合该问题，不要调用任何工具，直接回复：无匹配工具
- 路由系统的建议仅供参考，可能不准确，以问题实际意图为准"""


def _passthrough(state: dict, reason: str) -> dict:
    """直通：不设置 resolved_params → direct_executor 回退旧行为
    （candidates[0] + question 透传）。"""
    return {**state, "_tool_selection": {"source": "passthrough", "reason": reason}}


def _build_user_prompt(query: str, valid_caps: list[str],
                       decision: dict, feedback: str = "") -> str:
    lines = [f"用户问题: {query}", "", "候选工具:"]
    for cap in valid_caps:
        schema = tool_registry.get_schema(cap) or {}
        fn = capability_to_function_name(cap)
        lines.append(f"- {fn}: {schema.get('description', '')}")

    top = None
    cands = decision.get("candidates") or [] if isinstance(decision, dict) else []
    if cands and isinstance(cands[0], dict) and cands[0].get("name"):
        top = cands[0]
    if top is not None:
        lines.append(
            f"\n路由建议（仅供参考）: {top.get('name')} "
            f"(置信度 {float(top.get('score', 0) or 0):.2f})"
        )
    if feedback:
        lines.append(f"\n【上次尝试失败，请修正】{feedback}")
    return "\n".join(lines)


def _converge_candidates(candidates: list) -> list[str]:
    """候选收敛：去重 + 已注册 + 截断到 MAX_FC_CANDIDATES。

    以 tool_registry 注册表为单一事实来源（ALL_CAPABILITIES 静态表缺
    competitor.analyze，不可作为校验依据）。
    """
    seen: set[str] = set()
    valid: list[str] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "")
        if name and name not in seen and tool_registry.get_node(name):
            seen.add(name)
            valid.append(name)
            if len(valid) >= MAX_FC_CANDIDATES:
                break
    return valid


def _select_via_fc(state: dict, valid_caps: list[str], t0: float) -> dict:
    """FC 选择主循环：最多 2 次尝试（首试 + 带反馈重试 1 次）。"""
    query = state.get("question", "")
    decision = state.get("route_decision") or {}
    tools, fn2cap = capabilities_to_tools(valid_caps)
    if not tools:
        return _passthrough(state, "schema_convert_failed")

    bound = llm.bind_tools(tools)
    feedback = ""
    for attempt in range(2):
        raw = safe_call_with_timeout(
            bound.invoke,
            timeout=TOOL_SELECTOR_LLM_TIMEOUT,
            default_value=None,
            error_message=f"[ToolSelector] LLM 超时 ({TOOL_SELECTOR_LLM_TIMEOUT}s)",
            input=[("system", _SYSTEM_PROMPT),
                   ("human", _build_user_prompt(query, valid_caps, decision, feedback))],
            max_tokens=TOOL_SELECTOR_LLM_MAX_TOKENS,
        )
        if raw is None:
            # 超时/异常是基础设施问题，重试大概率同样超时 → 直接回退直通
            logger.warning("[ToolSelector] LLM 超时/异常，passthrough")
            return _passthrough(state, "llm_failed")

        tool_calls = getattr(raw, "tool_calls", None) or []
        if not tool_calls:
            # 模型明确不调工具（无匹配 / 降级话术 / 纯文本）→ 保守直通
            content = (getattr(raw, "content", "") or "")[:100]
            logger.info(f"[ToolSelector] 模型未调用工具: {content}")
            return {**state, "_tool_selection": {
                "source": "no_match", "candidates": valid_caps,
                "elapsed_ms": int((time.time() - t0) * 1000),
            }}

        tc = tool_calls[0]
        fn = tc.get("name", "")
        args = dict(tc.get("args") or {})
        cap = fn2cap.get(fn)
        if cap is None:
            feedback = f"工具 {fn} 不在候选列表内，只能从候选工具中选择。"
            logger.warning(f"[ToolSelector] 越界选择 {fn}，重试")
            continue

        schema = tool_registry.get_schema(cap)
        err = validate_params(schema["params"], args) if schema else None
        if err:
            feedback = f"参数校验失败: {err}"
            logger.warning(f"[ToolSelector] {cap} 参数校验失败: {err}，重试")
            continue

        params = args or {"question": query}
        # 模型选中的候选提到首位（skill_executor 取 candidates[0]）
        rest = [c for c in (decision.get("candidates") or [])
                if isinstance(c, dict) and c.get("name") != cap]
        new_decision = {**decision, "candidates": [{"name": cap, "score": 0.95}] + rest}
        logger.info(
            f"[ToolSelector] FC 选定 {cap} params={list(params.keys())} "
            f"(attempt={attempt + 1}, {int((time.time() - t0) * 1000)}ms)"
        )
        return {
            **state,
            "route_decision": new_decision,
            "resolved_params": params,
            "_tool_selection": {
                "source": "fc", "capability": cap, "params": params,
                "candidates": valid_caps, "attempts": attempt + 1,
                "elapsed_ms": int((time.time() - t0) * 1000),
            },
        }

    return _passthrough(state, "fc_invalid_after_retry")


def tool_selector_node(state: dict) -> dict:
    """direct 模式入口：路由缩候选 → FC 选工具 + 填参 → skill_executor。

    Returns:
        state 更新：resolved_params（FC 成功时）+ route_decision（候选重排）
        + _tool_selection（供 events 层发 log 事件；不在 state schema 内，
        仅随 stream update 透出，与 supervisor 的 _ready_dispatch 同模式）
    """
    t0 = time.time()

    if not ENABLE_FC_TOOL_SELECTION:
        return _passthrough(state, "flag_off")

    if state.get("route_mode") != "direct":
        return _passthrough(state, "not_direct")

    decision = state.get("route_decision") or {}
    candidates = decision.get("candidates", []) if isinstance(decision, dict) else []
    if not candidates:
        return _passthrough(state, "no_candidates")

    top = candidates[0] if isinstance(candidates[0], dict) else {}
    top_name = top.get("name", "")
    top_score = float(top.get("score", 0.0) or 0.0)
    if top_name in FAST_PATH_CAPS and top_score >= FAST_PATH_SCORE:
        return _passthrough(state, "fast_path")

    valid_caps = _converge_candidates(candidates)
    if not valid_caps:
        return _passthrough(state, "no_valid_candidates")

    return _select_via_fc(state, valid_caps, t0)
