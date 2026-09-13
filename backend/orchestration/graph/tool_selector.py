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

import hashlib
import time

from backend.config import (
    ENABLE_FC_TOOL_SELECTION,
    FC_TOOL_SELECTION_ALLOWLIST,
    FC_TOOL_SELECTION_ROLLOUT_PERCENT,
    TOOL_SELECTOR_FAST_PATH_SCORE,
    TOOL_SELECTOR_LLM_MAX_TOKENS,
    TOOL_SELECTOR_LLM_TIMEOUT,
    TOOL_SELECTOR_MAX_CANDIDATES,
    TOOL_SELECTOR_MODEL,
)
from backend.infra.llm import llm
from backend.infra.llm.proxy import bind_tools_for_model, get_active_model_name
from backend.infra.timeout import safe_call_with_timeout
from backend.orchestration.tool_registry import tool_registry
from backend.orchestration.tool_schema import (
    capabilities_to_tools,
    capability_to_function_name,
)
from backend.shared.logger import logger
from backend.skills.base import validate_params

# 参数平凡（只有 question）/自动注入（business.analyze 的 sql_result 走
# previous_outputs）的高频能力：路由高置信时直通，避免无意义的 LLM 调用。
# 阈值与候选上限可经 env 校准（评测数据说话后调，见 run_tool_selector_eval）
FAST_PATH_CAPS = {"sql.query", "rag.search", "business.analyze"}
FAST_PATH_SCORE = TOOL_SELECTOR_FAST_PATH_SCORE
MAX_FC_CANDIDATES = TOOL_SELECTOR_MAX_CANDIDATES

_SYSTEM_PROMPT = """你是电商运营平台的工具选择器。根据用户问题，从候选工具中选出最合适的一个，并从问题中抽取该工具需要的全部参数。

规则：
- 只能调用候选列表中的工具，禁止调用其他任何工具
- 参数值必须来自用户问题，禁止编造；问题中没有的信息不要填
- 候选列表由上游路由系统筛选得出，至少一个候选与问题相关。若多个候选都可能相关，选择与问题核心意图最匹配的一个，不要拒绝选择
- 仅当问题与所有候选明显无关时，才不调用任何工具，直接回复：无匹配工具
- 直接发起工具调用，回复中不要写分析推理过程
- 路由建议的分值仅供参考，以问题实际意图为准"""


def _record(source: str, reason: str = "", capability: str = "",
            t0: float | None = None) -> None:
    """指标埋点（软失败不影响主流程；直通路径不记 latency——无意义）。"""
    try:
        from backend.observability.metrics import record_tool_selection
        record_tool_selection(
            source, reason, capability,
            elapsed_ms=int((time.time() - t0) * 1000) if t0 is not None else None,
        )
    except Exception:
        pass


def _passthrough(state: dict, reason: str) -> dict:
    """直通：不设置 resolved_params → direct_executor 回退旧行为
    （candidates[0] + question 透传）。"""
    _record("passthrough", reason)
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


def _parse_text_tool_call(content: str, fn2cap: dict[str, str]) -> tuple[str, dict] | None:
    """文本兜底：模型把工具调用写成 JSON 文本而非 tool_calls 结构时解析。

    实测发现（评测 TS-040）：部分模型偶尔输出 ```json {"tool": ...,
    "parameters": ...}``` 或 {"name": ..., "arguments": ...}。解析出的
    工具名必须命中候选映射，参数走与 fc 相同的校验；解析失败返回 None。
    """
    import json
    import re

    from backend.shared.json_extractor import extract_json

    if not content or ("{" not in content):
        return None
    parsed = extract_json(content)
    if not isinstance(parsed, dict):
        return None
    name = parsed.get("tool") or parsed.get("name") or parsed.get("function")
    if not isinstance(name, str):
        return None
    cap = fn2cap.get(name)
    if cap is None:
        return None
    args = parsed.get("parameters") or parsed.get("arguments") or {}
    return cap, args if isinstance(args, dict) else {}


def _select_via_fc(state: dict, valid_caps: list[str], t0: float) -> dict:
    """FC 选择入口：包 LLM span（trace 瀑布图中可见耗时/token/模型），
    决策逻辑在 _fc_decide。"""
    from backend.observability.tracer import SpanKind, trace_collector

    span = None
    try:
        active = trace_collector.current()
        if active is not None:
            span = trace_collector.start_span(
                "tool_selector_llm", parent_id="tool_selector",
                name="工具选择 LLM", kind=SpanKind.LLM.value,
                input={
                    "query": (state.get("question") or "")[:200],
                    "candidates": valid_caps,
                    "model": TOOL_SELECTOR_MODEL or get_active_model_name(),
                },
            )
    except Exception:
        span = None  # 埋点软失败不影响决策

    result = _fc_decide(state, valid_caps, t0)
    if span is not None:
        try:
            sel = result.get("_tool_selection") or {}
            source = sel.get("source", "")
            status = "success" if source in ("fc", "no_match") else "failed"
            output = {"decision": source, "reason": sel.get("reason", "")}
            metrics = {"elapsed_ms": sel.get("elapsed_ms", int((time.time() - t0) * 1000))}
            if source == "fc":
                output["capability"] = sel.get("capability")
                output["attempts"] = sel.get("attempts", 1)
                output["params"] = sel.get("params")
            trace_collector.end_span(span, output=output, metrics=metrics,
                                     status=status)
        except Exception:
            pass  # span 收尾软失败
    return result


def _fc_decide(state: dict, valid_caps: list[str], t0: float) -> dict:
    """FC 决策主循环：最多 2 次尝试（首试 + 带反馈重试 1 次）。"""
    query = state.get("question", "")
    decision = state.get("route_decision") or {}
    tools, fn2cap = capabilities_to_tools(valid_caps)
    if not tools:
        return _passthrough(state, "schema_convert_failed")

    # 专用轻量模型优先（选择+填参小任务），未配置/不可用回退全局模型；
    # 两者都经 _BoundLLMProxy 走限流/韧性链/token 记录
    bound = bind_tools_for_model(TOOL_SELECTOR_MODEL, tools) or llm.bind_tools(tools)
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
            # 文本兜底：模型把调用写成了 JSON 文本
            content = getattr(raw, "content", "") or ""
            recovered = _parse_text_tool_call(content, fn2cap)
            if recovered is not None:
                cap, args = recovered
                logger.info(f"[ToolSelector] 文本兜底解析出工具调用: {cap}")
            else:
                # 模型明确不调工具（无匹配 / 降级话术 / 纯文本）→ 保守直通
                logger.info(f"[ToolSelector] 模型未调用工具: {content[:100]}")
                elapsed_ms = int((time.time() - t0) * 1000)
                _record("no_match", "model_declined", t0=t0)
                return {**state, "_tool_selection": {
                    "source": "no_match", "candidates": valid_caps,
                    "elapsed_ms": elapsed_ms,
                }}
        else:
            tc = tool_calls[0]
            cap = fn2cap.get(tc.get("name", ""))
            args = dict(tc.get("args") or {})
            if cap is None:
                feedback = f"工具 {tc.get('name')} 不在候选列表内，只能从候选工具中选择。"
                logger.warning(f"[ToolSelector] 越界选择 {tc.get('name')}，重试")
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
        elapsed_ms = int((time.time() - t0) * 1000)
        _record("fc", "ok", capability=cap, t0=t0)
        logger.info(
            f"[ToolSelector] FC 选定 {cap} model={TOOL_SELECTOR_MODEL or get_active_model_name()} "
            f"params={list(params.keys())} "
            f"(attempt={attempt + 1}, {elapsed_ms}ms)"
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


def _in_rollout(session_id: str) -> bool:
    """灰度判定（照 cs_prefilter 模式）：白名单 session 优先，其次
    md5 稳定哈希百分比。用 md5 而非内置 hash——内置 hash 有随机盐，
    进程重启会改变分组。"""
    if session_id in FC_TOOL_SELECTION_ALLOWLIST:
        return True
    if FC_TOOL_SELECTION_ROLLOUT_PERCENT >= 100:
        return True
    digest = int(hashlib.md5(session_id.encode()).hexdigest(), 16)
    return (digest % 100) < FC_TOOL_SELECTION_ROLLOUT_PERCENT


def tool_selector_node(state: dict) -> dict:
    """direct 模式入口：路由缩候选 → FC 选工具 + 填参 → skill_executor。

    Returns:
        state 更新：resolved_params（FC 成功时）+ route_decision（候选重排）
        + _tool_selection（供 events 层发 log 事件；不在 state schema 内，
        仅随 stream update 透出，与 supervisor 的 _ready_dispatch 同模式）
    """
    result = _decide(state)
    _write_trace_metadata(result)
    return result


def _decide(state: dict) -> dict:
    """决策主体（门控 → 直通 / FC），trace metadata 由出口统一写入。"""
    t0 = time.time()

    if not ENABLE_FC_TOOL_SELECTION:
        return _passthrough(state, "flag_off")

    if state.get("route_mode") != "direct":
        return _passthrough(state, "not_direct")

    # 灰度放量：未命中的 session 走 control 组（直通 = 旧行为），
    # 便于按 session 对比 FC 与直通的表现
    if not _in_rollout(state.get("session_id", "default")):
        return _passthrough(state, "rollout_skip")

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


def _write_trace_metadata(result: dict) -> None:
    """把选择决策快照写入 trace.metadata（所有路径都写，含直通）。

    解决"为什么选了这个工具/为什么直通"的单请求排查——此前只有
    SSE log 事件与 Prometheus 计数，翻单个请求的决策上下文要拼日志。
    埋点软失败不影响决策结果。
    """
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is None:
            return
        sel = result.get("_tool_selection") or {}
        trace.metadata["tool_selection"] = {
            "source": sel.get("source", ""),
            "reason": sel.get("reason", ""),
            "capability": sel.get("capability"),
            "params": sel.get("params"),
            "candidates": sel.get("candidates", []),
            "attempts": sel.get("attempts"),
            "model": TOOL_SELECTOR_MODEL or get_active_model_name(),
            "elapsed_ms": sel.get("elapsed_ms"),
        }
    except Exception:
        pass
