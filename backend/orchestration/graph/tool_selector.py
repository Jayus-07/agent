"""tool_selector.py — direct 模式的 FC 工具选择节点（tool RAG / 动态工具暴露）

三层路由（rule→vector→LLM）已把候选缩到 top-K；本节点把 K 个候选以
bind_tools 形式暴露给模型，由模型在候选内选一个并同步填参——即
"先路由缩候选，再给模型选"。取代 direct 模式"盲取 candidates[0] +
question 透传"的旧行为。

门控（route_mode=direct）：
  - routing policy 明确许可的 fast_path 再次验证后直通
  - llm_selection 灰区始终通过域内动态注册候选执行 FC 选择

降级策略：
  - 多候选时 LLM 超时/异常/无 tool_calls/校验重试失败 → 澄清，不执行首项
  - 单候选也必须有明确的 FC match；模型未选择时不能硬执行
  - 选择越界/参数校验失败 → 带反馈重试 1 次
"""
from __future__ import annotations

import time

from backend.config import (
    TOOL_SELECTOR_LLM_MAX_TOKENS,
    TOOL_SELECTOR_LLM_TIMEOUT,
    TOOL_SELECTOR_MAX_CANDIDATES,
    TOOL_SELECTOR_MODEL,
)
from backend.config import model_roles
from backend.infra.llm import llm
from backend.infra.llm.proxy import bind_tools_for_model, get_active_model_name
from backend.infra.timeout import safe_call_with_timeout
from backend.orchestration.capability_registry import tool_registry
from backend.orchestration.tool_schema import (
    capabilities_to_tools,
    capability_to_function_name,
)
from backend.shared.logger import logger
from backend.skills.base import _deadline_from_state, validate_params

MAX_FC_CANDIDATES = TOOL_SELECTOR_MAX_CANDIDATES


def _configured_tool_selector_model() -> str:
    """读取工具选择角色；无 DB 覆盖时保留历史模块常量语义。"""
    return model_roles.resolve_runtime_name("tool_selector", TOOL_SELECTOR_MODEL)


def _selector_timeout() -> int:
    """FC 外层超时闸：角色策略（llm_model_role_policy）优先，无 DB 行回落
    历史缺省 TOOL_SELECTOR_LLM_TIMEOUT(8s)。

    2026-09-22 实机验证：策略此前只在管理端回显、运行时零消费（假开关），
    Qwen3-8B 灰区选择 8s 内跑不完 → 3 连超时。管理员在角色策略里调大后
    经 refresh_registry 15s 刷新循环准实时生效。
    """
    try:
        if model_roles.has_db_policy("tool_selector"):
            return max(1, model_roles.resolve_runtime_policy("tool_selector").timeout_seconds)
    except Exception:
        pass
    return TOOL_SELECTOR_LLM_TIMEOUT


def _selector_llm_retries() -> int:
    """LLM 调用层重试次数：仅 DB 显式策略行生效（无行 = 0，保持现行为）。"""
    try:
        if model_roles.has_db_policy("tool_selector"):
            return max(0, model_roles.resolve_runtime_policy("tool_selector").max_retries)
    except Exception:
        pass
    return 0

# 系统提示词已收编进 prompt 注册表（key=router.tool_selector，2026-10-06）：
# 运行时经 PromptService 渲染（版本治理/trace 记账/请求级 pin）；
# 常量保留为注册表不可用时的降级兜底（与 YAML default 逐字一致，
# 漂移守卫见 tests/prompts/test_bare_prompt_collection.py）。
_SYSTEM_PROMPT = """你是电商运营平台的工具选择器。根据用户问题，从候选工具中选出最合适的一个，并从问题中抽取该工具需要的全部参数。

规则：
- 只能调用候选列表中的工具，禁止调用其他任何工具
- 参数值必须来自用户问题，禁止编造；问题中没有的信息不要填
- 候选列表由上游路由系统筛选得出，至少一个候选与问题相关。若多个候选都可能相关，选择与问题核心意图最匹配的一个，不要拒绝选择
- 仅当问题与所有候选明显无关时，才不调用任何工具，直接回复：无匹配工具
- 直接发起工具调用，回复中不要写分析推理过程
- 路由建议的分值仅供参考，以问题实际意图为准"""


def _selector_system_prompt() -> str:
    """系统提示词：注册表优先，异常降级模块常量（软失败）。"""
    try:
        from backend.prompts.service import prompt_service

        return prompt_service.render_sync("router.tool_selector").text
    except Exception as exc:  # noqa: BLE001 — prompt 读取失败不得阻断选择
        logger.warning(f"[ToolSelector] 注册表渲染失败，降级内置常量: {exc}")
        return _SYSTEM_PROMPT


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


def _clarify_selection(state: dict, reason: str, candidates: list[str]) -> dict:
    """多候选选择失败时阻断执行，交给上层输出澄清提示。"""
    _record("clarify", reason)
    return {
        **state,
        "selection_blocked": True,
        "clarification_request": {
            "question": "请补充一下您的需求，以便确定执行能力。",
            "options": list(candidates),
            "source": reason,
        },
        "_tool_selection": {
            "source": "clarify",
            "reason": reason,
            "candidates": candidates,
            "next_action": "clarify",
        },
    }


def _router_top_candidate_fallback(state: dict, reason: str) -> dict | None:
    """保留旧导入名，但彻底禁用按候选分数自动执行。"""

    logger.info(
        "[ToolSelector] 忽略已废弃的首候选兜底: reason=%s candidates=%s",
        reason,
        len((state.get("route_decision") or {}).get("candidates") or []),
    )
    return None


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
        route_meta = decision.get("routing_meta") or {}
        score_type = str(route_meta.get("score_type") or "unknown")
        lines.append(
            f"\n路由候选建议（仅供参考）: {top.get('name')} "
            f"(分数 {float(top.get('score', 0) or 0):.2f}, 类型 {score_type})"
        )
    if feedback:
        lines.append(f"\n【上次尝试失败，请修正】{feedback}")
    return "\n".join(lines)


def _converge_candidates(candidates: list, domain: str = "") -> list[str]:
    """候选收敛：去重 + 当前域已注册 + 截断到 MAX_FC_CANDIDATES。

    以 tool_registry 注册表为单一事实来源（ALL_CAPABILITIES 静态表缺
    competitor.analyze，不可作为校验依据）。
    """
    allowed_for_domain: set[str] | None = None
    if domain:
        try:
            from backend.orchestration.router.capability_router import _DOMAIN_ALIASES
            from backend.orchestration.router.hierarchical import resolve_domain_tools

            canonical = _DOMAIN_ALIASES.get(domain, domain)
            allowed_for_domain = {c.name for c in resolve_domain_tools(canonical)}
        except Exception:
            allowed_for_domain = set()
    else:
        try:
            from backend.orchestration.router.manifest import load_manifest

            allowed_for_domain = {c.name for c in load_manifest().routed_capabilities}
        except Exception:
            allowed_for_domain = set()
    seen: set[str] = set()
    valid: list[str] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "")
        if (name and name not in seen and tool_registry.get_node(name)
                and name in allowed_for_domain):
            seen.add(name)
            valid.append(name)
            if len(valid) >= MAX_FC_CANDIDATES:
                break
    return valid


# ── selector 结果校验（路由专项 Step 2：candidate constraints → validation）──

def _capability_domain(cap: str) -> str:
    """capability 的粗域声明（capabilities.yaml 唯一事实源）；未声明返回空。"""
    try:
        from backend.orchestration.router.manifest import load_manifest

        for c in load_manifest().capabilities:
            if c.name == cap:
                return c.domain
    except Exception:
        pass
    return ""


def _validate_selection(cap: str, valid_caps: list[str],
                        domain: str) -> str | None:
    """FC 返回 capability 的合法性校验。返回拒绝原因，None = 通过。

    两层（最小保护，不改 Router / 不扩规则集）：
      1. candidate constraints —— 必须在本次绑定的候选集合内；
      2. domain conflict —— 与当前请求粗域冲突（如 data 域问题选中
         knowledge 域能力）即拒绝。state 无域信息（兼容短路）时跳过。
    合法但次优（如 sql 与 data.collect 都在域内）不拦 —— 优劣由校准与
    FC 意图判断负责，这里只拦非法。
    """
    if cap not in valid_caps:
        return "not_in_candidates"
    if domain:
        try:
            from backend.orchestration.router.capability_router import _DOMAIN_ALIASES
            from backend.orchestration.router.hierarchical import resolve_domain_tools

            canonical = _DOMAIN_ALIASES.get(domain, domain)
            if cap not in {item.name for item in resolve_domain_tools(canonical)}:
                return f"domain_conflict:{_capability_domain(cap)}!={canonical}"
        except Exception:
            return "domain_registry_validation_failed"
    return None


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
    parsed = extract_json(content, source="router.tool_selector")
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
                    "model": _configured_tool_selector_model() or get_active_model_name(),
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


def _selector_deadline_log(deadline, decision: str, reason: str) -> None:
    """selector 预算日志（P1 阶段 2）：selector_elapsed / selector_budget /
    remaining_budget / deadline_decision / deadline_reason 统一落日志。"""
    try:
        if deadline is None:
            return
        logger.info(
            "[ToolSelector][Deadline] %s",
            deadline.budget_log_fields(
                selector_elapsed_ms=deadline.elapsed_ms(),
                decision=decision, reason=reason,
            ),
        )
    except Exception:
        pass


def _selector_degrade(state: dict, reason: str) -> dict:
    """selector 预算耗尽时澄清阻断，不继续消耗工具执行预算。"""

    _selector_deadline_log(_deadline_from_state(state), "selector_skip", reason)
    candidates = [
        str(item.get("name"))
        for item in ((state.get("route_decision") or {}).get("candidates") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    return _clarify_selection(state, reason, candidates)


def _fc_decide(state: dict, valid_caps: list[str], t0: float) -> dict:
    """FC 决策主循环：最多 2 次尝试（首试 + 带反馈重试 1 次）。

    P1 阶段 2：selector 只能消费 selector_budget（35% × T，绝对窗口），
    每次尝试前重算剩余，耗尽即降级 —— 不得侵占工具执行保底预算。
    """
    query = state.get("question", "")
    decision = state.get("route_decision") or {}
    tools, fn2cap = capabilities_to_tools(valid_caps)
    if not tools:
        return _clarify_selection(state, "schema_convert_failed", valid_caps)

    deadline = _deadline_from_state(state)
    selector_budget_ms = deadline.selector_budget_ms if deadline is not None else None

    # selector 预算闸（入口检查）：预算耗尽直接降级，连 LLM 客户端都不建 ——
    # 不消耗任何下游资源
    if deadline is not None and deadline.selector_remaining_ms() <= 0:
        logger.warning(
            "[ToolSelector][Deadline] selector 预算耗尽，降级 %s",
            deadline.budget_log_fields(
                selector_elapsed_ms=deadline.elapsed_ms(),
                decision="selector_skip",
                reason="selector_budget_exhausted",
            ),
        )
        return _clarify_selection(state, "selector_budget_exhausted", valid_caps)

    # 专用轻量模型优先（选择+填参小任务），未配置/不可用回退全局模型；
    # 两者都经 _BoundLLMProxy 走限流/韧性链/token 记录
    bound = bind_tools_for_model(_configured_tool_selector_model(), tools) or llm.bind_tools(tools)
    timeout_s = _selector_timeout()
    llm_retries = _selector_llm_retries()
    feedback = ""
    for attempt in range(2):
        # selector 预算闸（每次尝试前重算剩余；绝对窗口 = 预算 − 请求已耗时）
        if deadline is not None:
            selector_remaining_ms = deadline.selector_remaining_ms()
            if selector_remaining_ms <= 0:
                logger.warning(
                    "[ToolSelector][Deadline] selector 预算耗尽，降级 %s",
                    deadline.budget_log_fields(
                        selector_elapsed_ms=deadline.elapsed_ms(),
                        decision="selector_skip",
                        reason="selector_budget_exhausted",
                    ),
                )
                return _clarify_selection(state, "selector_budget_exhausted", valid_caps)
            # 单次尝试超时同时被角色策略与 selector 剩余预算封顶
            timeout_s = min(timeout_s, selector_remaining_ms / 1000)

        # LLM 调用层重试（角色策略 max_retries）：超时/异常逐次重试，仍失败
        # 才落到底下的业务级反馈重试 / clarify
        raw = None
        for _llm_attempt in range(llm_retries + 1):
            raw = safe_call_with_timeout(
                bound.invoke,
                timeout=timeout_s,
                default_value=None,
                error_message=f"[ToolSelector] LLM 超时 ({timeout_s}s)",
                input=[("system", _selector_system_prompt()),
                       ("human", _build_user_prompt(query, valid_caps, decision, feedback))],
                max_tokens=TOOL_SELECTOR_LLM_MAX_TOKENS,
            )
            if raw is not None:
                break
        if raw is None:
            # FC 故障时不能因候选分数或候选数量自动执行。
            logger.warning("[ToolSelector] LLM 超时/异常")
            _selector_deadline_log(deadline, "selector_timeout", "llm_failed")
            return _clarify_selection(state, "llm_failed", valid_caps)

        tool_calls = getattr(raw, "tool_calls", None) or []
        fit = "match"
        reason = ""
        if not tool_calls:
            # 文本兜底：模型把调用写成了 JSON 文本
            content = getattr(raw, "content", "") or ""
            recovered = _parse_text_tool_call(content, fn2cap)
            if recovered is not None:
                cap, args = recovered
                logger.info(f"[ToolSelector] 文本兜底解析出工具调用: {cap}")
            else:
                # 多候选时模型明确不调工具不能解释为首项可执行。
                logger.info(f"[ToolSelector] 模型未调用工具: {content[:100]}")
                elapsed_ms = int((time.time() - t0) * 1000)
                _record("no_match", "model_declined", t0=t0)
                return {
                    **state,
                    "selection_blocked": True,
                    "_tool_selection": {
                        "source": "no_match", "candidates": valid_caps,
                        "reason": "model_declined",
                        "next_action": "clarify",
                        "elapsed_ms": elapsed_ms,
                    },
                }
        else:
            tc = tool_calls[0]
            cap = fn2cap.get(tc.get("name", ""))
            args = dict(tc.get("args") or {})
            fit = tc.get("fit", args.pop("_fit", "match"))
            reason = str(tc.get("reason") or args.pop("_reason", ""))
            if cap is None:
                feedback = f"工具 {tc.get('name')} 不在候选列表内，只能从候选工具中选择。"
                logger.warning(f"[ToolSelector] 越界选择 {tc.get('name')}，重试")
                continue
            if fit not in {"match", "no_match"}:
                return _clarify_selection(state, "invalid_intent_fit", valid_caps)
            if fit == "no_match":
                return _clarify_selection(
                    state, "tool_intent_mismatch", valid_caps
                )

        # selector 结果校验（Step 2）：非法选择直接拒绝进现有 fallback，
        # 不做带反馈重试（重试只会教模型钻规则，不会改变合法性）
        reject_reason = _validate_selection(cap, valid_caps,
                                            str(state.get("domain") or ""))
        if reject_reason:
            logger.warning(
                f"[ToolSelector] 非法选择 {cap}（{reject_reason}），直接拒绝")
            _record("rejected", reject_reason, capability=cap)
            return _clarify_selection(
                state, f"selection_rejected:{reject_reason}", valid_caps
            )

        schema = tool_registry.get_schema(cap)
        err = validate_params(schema["params"], args) if schema else None
        if err:
            feedback = f"参数校验失败: {err}"
            logger.warning(f"[ToolSelector] {cap} 参数校验失败: {err}，重试")
            continue

        # 只有声明了 question 的 capability 才能使用旧的 question 回退。
        # auto-only capability（如 business.analyze）必须等待运行时注入，
        # 不能把 question 伪装成其参数，也不能接受模型主动传 auto 字段。
        schema_params = (tool_registry.get_schema(cap) or {}).get("params") or {}
        params = args or ({"question": query} if "question" in schema_params else {})
        # 模型只决定候选顺序；保留路由器的原始分数，不伪造置信分。
        original = [c for c in (decision.get("candidates") or [])
                    if isinstance(c, dict)]
        chosen = next((dict(c) for c in original if c.get("name") == cap), {"name": cap})
        rest = [c for c in original if c.get("name") != cap]
        new_decision = {**decision, "candidates": [chosen] + rest}
        elapsed_ms = int((time.time() - t0) * 1000)
        _record("fc", "ok", capability=cap, t0=t0)
        _selector_deadline_log(deadline, "selector_done", f"fc_selected:{cap}")
        logger.info(
            f"[ToolSelector] FC 选定 {cap} model={_configured_tool_selector_model() or get_active_model_name()} "
            f"params={list(params.keys())} "
            f"(attempt={attempt + 1}, {elapsed_ms}ms)"
        )
        return {
            **state,
            "route_decision": new_decision,
            "resolved_params": params,
            # 分层路由平铺字段（§12）：FC 选定后回写，trace/评测消费
            "selected_tool": cap,
            "tool_route_mode": state.get("tool_route_mode") or "llm_selection",
            "_tool_selection": {
                "source": "fc", "capability": cap, "params": params,
                "candidates": valid_caps, "attempts": attempt + 1,
                "fit": "match", "selection_reason": reason,
                "elapsed_ms": int((time.time() - t0) * 1000),
            },
        }

    return _clarify_selection(state, "fc_invalid_after_retry", valid_caps)


def tool_selector_node(state: dict) -> dict:
    """direct 模式入口：路由缩候选 → FC 选工具 + 填参 → skill_executor。

    Returns:
        state 更新：resolved_params（FC 成功时）+ route_decision（候选重排）
        + _tool_selection（供 events 层发 log 事件；不在 state schema 内，
        仅随 stream update 透出，与 supervisor 的 _ready_dispatch 同模式）
    """
    from backend.orchestration.graph.sse_event_sink import emit_sse_progress

    emit_sse_progress(
        node="tool_selector", phase="capability_selection",
        message="正在选择合适的查询能力",
    )
    result = _decide(state)
    _write_trace_metadata(result)
    return result


def _decide(state: dict) -> dict:
    """统一门禁后的 selector：Fast Path 验证后直通，其余只走受限 FC。"""
    t0 = time.time()
    if state.get("route_mode") != "direct":
        return _passthrough(state, "not_direct")

    decision = state.get("route_decision") or {}
    candidates = decision.get("candidates", []) if isinstance(decision, dict) else []
    route_meta = decision.get("routing_meta") or {}
    selection_mode = str(state.get("tool_route_mode") or route_meta.get("selection_mode") or "")
    valid_caps = _converge_candidates(candidates, str(state.get("domain") or route_meta.get("domain") or ""))

    if selection_mode == "fast_path":
        selected = str(state.get("selected_tool") or route_meta.get("selected_tool") or "")
        top = next((row for row in candidates if isinstance(row, dict) and row.get("name") == selected), None)
        try:
            from backend.orchestration.router.execution_mode import ExecutionModeResolver

            gate_decision = {
                "selection_mode": selection_mode,
                "top1_score": route_meta.get("fine_top1_score"),
                "top2": route_meta.get("fine_top2"),
                "top2_score": route_meta.get("fine_top2_score"),
                "margin": route_meta.get("fine_margin"),
                "score_type": route_meta.get("score_type"),
                "candidates": candidates,
            }
            gate_reason = ExecutionModeResolver()._fast_path_block_reason(
                str(state.get("domain") or route_meta.get("domain") or ""),
                selected,
                gate_decision,
                top,
            )
            valid_fast = bool(selected) and selected in valid_caps and not gate_reason
        except Exception:
            valid_fast = False
        if valid_fast:
            result = _passthrough(state, "validated_hierarchical_fast_path")
            result["_tool_selection"].update({"capability": selected, "candidates": [selected]})
            return result
        return _clarify_selection(state, "fast_path_gate_mismatch", valid_caps)

    if selection_mode != "llm_selection":
        return _clarify_selection(state, "selection_mode_missing_or_invalid", valid_caps)
    if not valid_caps:
        return _clarify_selection(state, "no_valid_candidates", [])

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
            "model": _configured_tool_selector_model() or get_active_model_name(),
            "elapsed_ms": sel.get("elapsed_ms"),
        }
        route = dict(trace.metadata.get("routing_decision") or {})
        route.update({
            "selection_mode": route.get("selection_mode") or (result.get("route_decision") or {}).get("routing_meta", {}).get("selection_mode", ""),
            "candidates": route.get("candidates") or sel.get("candidates", []),
            "source": route.get("source") or sel.get("source", ""),
            "candidate_source": route.get("candidate_source") or route.get("source") or "",
            "selection_source": sel.get("source", ""),
            "score_type": route.get("score_type") or (result.get("route_decision") or {}).get("routing_meta", {}).get("score_type", "unknown"),
            "margin": route.get("margin", 0.0),
            "block_reason": sel.get("reason") or route.get("block_reason", ""),
            "selection_block_reason": sel.get("reason", "") if result.get("selection_blocked") else "",
            "selected_tool": sel.get("capability") or (result.get("selected_tool") or ""),
            "final_destination": "clarify" if result.get("selection_blocked") else (
                "skill_executor" if sel.get("source") in {"fc", "passthrough"} else "clarify"
            ),
            "route_policy_version": route.get("route_policy_version", "routing-p0-1"),
        })
        trace.metadata["routing_decision"] = route
    except Exception:
        pass
