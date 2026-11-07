"""cs.py — CS Graph 评测 Runner

评估对象：CS Graph（customer_service/graph_builder.py）的路由与应答。
判定维度：
  - 路由正确性：supervisor 是否把会话派给 expected.target 对应的 expert
  - 应答有效性：final_answer 非空 + must_contain 关键词命中

offline 模式：只做数据集结构 sanity（不调 graph），CI 无 LLM Key 也可跑。
live 模式：逐条 invoke CS Graph（thread_id 按 case id 隔离）。
"""
import time
import uuid
from contextlib import nullcontext

# cs_target（Router 预过滤产物）→ CS Graph expert 节点名
# 单一事实源在 customer_service/graph_state.py（cs_graph_node 同用此映射）
from backend.customer_service.graph_state import CS_TARGET_TO_EXPERT as _TARGET_TO_EXPERT
from backend.customer_service.graph_state import EXPERT_NAME_TO_NODE as _EXPERT_NAME_TO_NODE
from backend.customer_service.router.intents import FINE_INTENTS as _FINE_INTENTS
from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner
from backend.shared.logger import logger

_VALID_TARGETS = set(_TARGET_TO_EXPERT)
_VALID_INTENTS = set(_FINE_INTENTS)
_VALID_NEXT_ACTION = {"answer", "clarify", "refuse", "handoff",
                      "propose", "propose_with_gap"}
_VALID_CS_ROUTE = {"hit", "miss_non_cs", "clarify_weak"}
_CS_EXPERT_NODES = {
    "cs_knowledge_expert", "cs_query_expert", "cs_action_expert",
    "cs_complaint_expert", "cs_handoff_expert",
}


def _user_turns(case: TestCase) -> list[str]:
    """按锁版顺序重放用户轮次；不将标注的助手回复伪装成系统输出。"""
    turns = case.metadata.get('turns')
    if not turns:
        return [case.question]
    messages = [
        str(turn.get('text', '')).strip()
        for turn in turns if turn.get('role') == 'user'
    ]
    if not messages or any(not message for message in messages):
        raise ValueError('多轮样本必须包含非空用户输入')
    return messages


def _run_cs(cases: list[TestCase], live: bool = False,
            mode: str = "graph_only", **kwargs) -> list[EvalResult]:
    if not live:
        return _run_offline_sanity(cases)
    if mode == "full_path":
        return _run_live_full_path(cases)
    return _run_live(cases)


def _run_offline_sanity(cases: list[TestCase]) -> list[EvalResult]:
    """数据集结构 sanity：expected.target 合法、id 唯一。不调 graph。"""
    results: list[EvalResult] = []
    seen_ids: set[str] = set()
    for case in cases:
        exp = case.expected
        problems: list[str] = []
        if case.id in seen_ids:
            problems.append(f"重复 case id: {case.id}")
        seen_ids.add(case.id)
        target = exp.get("target")
        if not target or target not in _VALID_TARGETS:
            problems.append(f"expected.target 非法: {target!r}（合法值 {_VALID_TARGETS}）")
        # cs-v2 扩展字段（P0 评测集，2026-09-19）：存在即校验；v1 用例无这些键自动跳过
        if exp.get("intent") is not None and exp["intent"] not in _VALID_INTENTS:
            problems.append(f"expected.intent 非法: {exp['intent']!r}")
        if exp.get("cs_route") is not None and exp["cs_route"] not in _VALID_CS_ROUTE:
            problems.append(f"expected.cs_route 非法: {exp['cs_route']!r}")
        if exp.get("next_action") is not None and exp["next_action"] not in _VALID_NEXT_ACTION:
            problems.append(f"expected.next_action 非法: {exp['next_action']!r}")
        if exp.get("risk_level") not in (None, "low", "medium", "high"):
            problems.append(f"expected.risk_level 非法: {exp['risk_level']!r}")
        if exp.get("should_handoff") is not None and not isinstance(
                exp["should_handoff"], bool):
            problems.append("expected.should_handoff 必须为 bool")
        results.append(EvalResult(
            case_id=case.id,
            module="cs",
            status="fail" if problems else "pass",
            expected=exp,
            actual={"problems": problems},
            error_msg="; ".join(problems) or None,
        ))
    return results


def _run_live(cases: list[TestCase]) -> list[EvalResult]:
    """逐条 invoke CS Graph，评估路由与应答。

    路由接线对齐生产：先经 CSRouter.route（规则层，P1-P3 门禁验证的
    同一入口）产出 cs_route 再喂图——生产链路（orchestration cs_prefilter）
    即由该结果驱动 supervisor L1；直接传空 dict 会旁路决策层，supervisor
    全靠 L3 LLM/L6 兜底重路由（b0r2 实测 expert_match 掉到 10%）。
    会话指代消解与 CSUnderstanding LLM 增补属会话软层，单轮评测样本不
    消费，不在此复刻。
    """
    from backend.config.customer_service import CS_GRAPH_RECURSION_LIMIT
    from backend.customer_service.graph_builder import get_cs_graph
    from backend.customer_service.graph_state import new_cs_graph_input
    from backend.customer_service.router.cs_router import get_cs_router

    # run 级隔离 token：thread_id/conversation_id 必须区分不同 run——
    # 固定 eval-cs-{case.id} 会让下一次 run 续上上一次的 checkpointer
    # 会话状态（b0r3 实测：complaint pending 补槽路径直接接管，45ms
    # 返回、expert_history 为空），且工单/投诉单按会话幂等去重也失真。
    run_token = uuid.uuid4().hex[:12]

    graph = get_cs_graph()
    results: list[EvalResult] = []
    for case in cases:
        t0 = time.time()
        exp = case.expected
        try:
            from backend.infra.llm.budget import (
                bind_request_budget,
                check_quota_blocked,
                clear_request_budget,
            )

            check_quota_blocked()
            # 请求级预算按 case 唯一：bind 复用同 ID 会让 0.50 元/32k token
            # 的单请求限额被整批累计（Review P2 同类发现）；批次归属与
            # ≤10 元上限由外层 scoped_run_budget 承担，二者解耦。
            bind_request_budget(
                f"eval-case:{run_token}:{case.id}",
                user_id="eval_bot", tenant_id="default",
            )
            try:
                # thread_id 按 run+case 隔离，避免评测样本间与跨 run 状态串扰
                config = {
                    "recursion_limit": CS_GRAPH_RECURSION_LIMIT,
                    "configurable": {"thread_id": f"eval-cs-{run_token}-{case.id}"},
                }
                messages = _user_turns(case)
                for message in messages:
                    check_quota_blocked()
                    # 同一case跨轮保留业务会话；不同case/run仍严格隔离。
                    cs_input = new_cs_graph_input(
                        user_message=message,
                        user_id="eval_bot",
                        session_id=f"cs_eval_{run_token}-{case.id}",
                        conversation_id=f"eval-cs-{run_token}-{case.id}",
                        cs_route=get_cs_router().route(message).model_dump(),
                    )
                    final_state = graph.invoke(cs_input, config=config)
            finally:
                clear_request_budget()
            duration_ms = int((time.time() - t0) * 1000)

            answer = (final_state.get("final_answer") or "").strip()
            expert_history = final_state.get("expert_history") or []
            # 现图契约（experts/base.py）条目为 {"expert": ...}；旧版 {"node": ...}
            # 与纯节点名字符串保持兼容，取不到名字的条目跳过（路由断言不允许
            # 把 None 计入 visited，否则期望专家永远"未命中"）。
            # 条目值是 expert 语义短名（knowledge/query/...），断言期望的是图
            # 节点名（cs_knowledge_expert/...），统一经权威映射表归一后再比对。
            visited = set()
            for entry in expert_history:
                if isinstance(entry, dict):
                    expert_name = entry.get("expert") or entry.get("node")
                else:
                    expert_name = entry
                if not expert_name:
                    continue
                visited.add(_EXPERT_NAME_TO_NODE.get(str(expert_name), str(expert_name)))
            expected_expert = _TARGET_TO_EXPERT.get(exp.get("target", ""), "")

            actual = {
                "final_answer": answer[:200],
                'turn_count': len(messages),
                "visited_experts": sorted(visited),
                "handoff_state": final_state.get("handoff_state", ""),
                "status": final_state.get("conversation_status", ""),
            }

            problems: list[str] = []
            metrics: dict[str, float | None] = {}
            if not answer:
                problems.append("final_answer 为空")
            must_contain = [k for k in (exp.get("must_contain") or []) if k]
            hit = sum(1 for kw in must_contain if kw.lower() in answer.lower())
            if must_contain:
                metrics["must_contain_hit"] = hit / len(must_contain)
                if hit < len(must_contain):
                    problems.append(f"must_contain 未命中: {[k for k in must_contain if k.lower() not in answer.lower()]}")
            if expected_expert and expected_expert not in visited:
                problems.append(
                    f"路由不符: 期望 {expected_expert}，实际命中 {sorted(visited)}")
            metrics["expert_match"] = 1.0 if expected_expert in visited else 0.0

            results.append(EvalResult(
                case_id=case.id,
                module="cs",
                status="fail" if problems else "pass",
                expected=exp,
                actual=actual,
                metrics=metrics,
                duration_ms=duration_ms,
                error_msg="; ".join(problems) or None,
            ))
        except Exception as e:
            logger.warning(f"[CS Runner] case {case.id} 执行失败: {e}", exc_info=True)
            results.append(EvalResult(
                case_id=case.id,
                module="cs",
                status="error",
                expected=exp,
                actual={},
                duration_ms=int((time.time() - t0) * 1000),
                error_msg=str(e)[:300],
            ))
    return results


register_runner("cs", _run_cs, needs_live=False)


def _run_live_full_path(cases: list[TestCase]) -> list[EvalResult]:
    """full_path 模式（P0 评测集设计稿 §4）：
    Input Guard → 主图 Router（含 CS/旅游预过滤）→ route_selector → CS 子图全链。

    结果使用生产 SSE 入口；请求级观察器只捕获客服终态动作和显式事实 ID。
    test-only scope 从锁版 KB fixture 与沙箱订单结构化字段生成事实 ID；
    概念级 forbidden_facts 仍需语义裁判，缺少证据的用例标为 skip。
    """
    from backend.customer_service.knowledge.evaluation_scope import (
        cs_knowledge_eval_scope,
    )
    from backend.orchestration.graph import MultiAgentSystem

    agent = MultiAgentSystem()
    run_token = uuid.uuid4().hex[:12]
    results: list[EvalResult] = []
    for case in cases:
        t0 = time.time()
        exp = case.expected
        nodes: set[str] = set()
        deltas: list[str] = []
        clarify_seen = False
        done_data: dict = {}
        last_observation: dict = {}
        case_fact_ids: set[str] = set()
        case_fact_ids_observable = False
        try:
            eval_scope_args = _cs_full_path_eval_scope_args(case)
            from backend.infra.llm.budget import (
                bind_request_budget,
                check_quota_blocked,
                clear_request_budget,
            )

            check_quota_blocked()
            # 同 _run_live：请求级预算按 case 唯一，批次归属走外层 run 作用域
            bind_request_budget(
                f"eval-case:{run_token}:{case.id}",
                user_id="eval_bot", tenant_id="default",
            )
            try:
                from backend.orchestration.graph.cs_graph_node import (
                    capture_cs_graph_evaluation,
                )

                messages = _user_turns(case)
                session_id = f'eval-cs-v2-{run_token}-{case.id}'
                for message in messages:
                    check_quota_blocked()
                    # 断言针对最后一轮；前轮的域进入/澄清不能替末轮通过。
                    nodes = set()
                    deltas = []
                    clarify_seen = False
                    done_data = {}
                    observations: list[dict] = []
                    scope_context = (
                        cs_knowledge_eval_scope(**eval_scope_args)
                        if eval_scope_args is not None else nullcontext()
                    )
                    with scope_context, capture_cs_graph_evaluation(
                        observations.append,
                    ):
                        for evt in agent.stream_events(
                            message,
                            session_id,
                            # 主图仍使用 default；只有客服知识服务在内部
                            # eval scope 内切到 cs_eval_kb，避免把测试库扩散
                            # 给主图的其它通用检索/工具。
                            kb_id="default",
                            user_id="eval_bot", tenant_id='default',
                        ):
                            name = evt.get("event")
                            data = evt.get("data") or {}
                            if name == "status" and data.get("node"):
                                nodes.add(data["node"])
                            elif name == "clarification":
                                clarify_seen = True
                            elif name == "delta" and isinstance(data.get("content"), str):
                                deltas.append(data["content"])
                            elif name == "done":
                                done_data = data
                            elif name == 'error':
                                raise RuntimeError('完整路径返回执行错误事件')
                    last_observation = observations[-1] if observations else {}
                    if last_observation.get("fact_ids_observable"):
                        case_fact_ids_observable = True
                    observed_turn_fact_ids = last_observation.get("fact_ids")
                    if isinstance(observed_turn_fact_ids, list):
                        case_fact_ids.update(
                            str(fact_id)
                            for fact_id in observed_turn_fact_ids
                            if fact_id
                        )
            finally:
                clear_request_budget()
            if not isinstance(done_data, dict):
                done_data = {}
            done_answer = done_data.get("answer")
            answer = (
                done_answer if isinstance(done_answer, str)
                else "".join(deltas)
            )
            source_ids = _source_ids(done_data.get("sources"))
            problems: list[str] = []
            unverified: list[str] = []
            metrics: dict[str, float | None] = {}
            route_matches = True
            if not answer.strip() and not clarify_seen:
                problems.append('末轮没有回答或澄清事件')
            entered_cs = "cs_graph_node" in nodes
            expected_route = exp.get("cs_route")
            if expected_route == "hit" and not entered_cs:
                problems.append(f"期望进入客服域但未进入: nodes={sorted(nodes)}")
                route_matches = False
            elif expected_route == "miss_non_cs" and entered_cs:
                problems.append("期望未命中客服域，但实际进入了 cs_graph_node")
                route_matches = False
            elif expected_route == "clarify_weak":
                if entered_cs:
                    problems.append("弱命中澄清不应进入 cs_graph_node")
                    route_matches = False
                if not clarify_seen:
                    problems.append("弱命中路由期望澄清，但没有 clarification 事件")
                    route_matches = False
            if expected_route is not None:
                metrics["cs_route_match"] = 1.0 if route_matches else 0.0

            observed_action = (
                "clarify" if clarify_seen
                else last_observation.get("next_action")
            )
            expected_action = exp.get("next_action")
            if expected_action is not None:
                if expected_action == "clarify" and not clarify_seen:
                    problems.append("期望澄清但末轮没有 clarification 事件")
                    metrics["next_action_match"] = 0.0
                elif observed_action is None:
                    unverified.append("next_action 缺少客服终态观察证据")
                elif observed_action != expected_action:
                    problems.append(
                        f"next_action 不符: 期望 {expected_action!r}，"
                        f"实际 {observed_action!r}"
                    )
                    metrics["next_action_match"] = 0.0
                else:
                    metrics["next_action_match"] = 1.0

            for kw in exp.get("must_contain") or []:
                if kw and kw not in answer:
                    problems.append(f"must_contain 未命中: {kw!r}")
            for kw in exp.get("must_not_contain") or []:
                if kw and kw.casefold() in answer.casefold():
                    problems.append(f"禁词命中: {kw!r}")

            expected_facts = exp.get("allowed_facts") or []
            observed_fact_ids = (
                sorted(case_fact_ids) if case_fact_ids_observable else None
            )
            fact_ids_observable = case_fact_ids_observable
            if expected_facts:
                if (
                    not fact_ids_observable
                    or observed_fact_ids is None
                    or not observed_fact_ids
                ):
                    unverified.append(
                        "allowed_facts 缺少与预期 KB/DB 锚点一致的运行时事实 ID"
                    )
                else:
                    disallowed_facts = sorted(
                        set(observed_fact_ids) - set(expected_facts)
                    )
                    if disallowed_facts:
                        problems.append(
                            f"运行时引用了未允许事实: {disallowed_facts}"
                        )
                    metrics["allowed_facts_verified"] = (
                        0.0 if disallowed_facts else 1.0
                    )

            if exp.get("forbidden_facts"):
                unverified.append(
                    "forbidden_facts 是语义类别，当前无固定语义裁判；"
                    "仅 must_not_contain 按字面精确检查"
                )
            metrics["cs_domain_entered"] = 1.0 if entered_cs else 0.0
            metrics["clarified"] = 1.0 if clarify_seen else 0.0
            actual = {
                "nodes": sorted(nodes),
                "answer_head": answer[:200],
                "turn_count": len(messages),
                "route_observed": (
                    "hit" if entered_cs else
                    "clarify_weak" if clarify_seen else "miss_non_cs"
                ),
                "next_action": observed_action,
                "source_ids": source_ids,
                "fact_ids": observed_fact_ids,
                "fact_ids_observable": bool(fact_ids_observable),
                "response_guard_result": last_observation.get(
                    "response_guard_result",
                ),
                "unverified_checks": unverified,
            }
            status = "fail" if problems else "skip" if unverified else "pass"
            results.append(EvalResult(
                case_id=case.id,
                module="cs",
                status=status,
                expected=exp,
                actual=actual,
                metrics=metrics,
                duration_ms=int((time.time() - t0) * 1000),
                error_msg="; ".join(
                    problems + [f"未验证: {item}" for item in unverified]
                ) or None,
            ))
        except Exception as e:
            logger.warning(f"[CS Runner] full_path case {case.id} 执行失败: {e}", exc_info=True)
            results.append(EvalResult(
                case_id=case.id,
                module="cs",
                status="error",
                expected=exp,
                actual={},
                duration_ms=int((time.time() - t0) * 1000),
                error_msg=str(e)[:300],
            ))
    return results


def _cs_full_path_eval_scope_args(case: TestCase) -> dict[str, str] | None:
    """从 suite metadata 建立受控客服 test KB 范围；不按请求文本推断。"""
    metadata = case.metadata or {}
    scope_keys = {"kb_id", "fixture_set", "suite", "dataset_version"}
    scope_identity_keys = {"kb_id", "fixture_set", "suite"}
    if not scope_identity_keys.intersection(metadata):
        # 兼容未使用 suite loader 的旧单例测试；这类运行不启用 test KB。
        return None
    if not all(str(metadata.get(key) or "").strip() for key in scope_keys):
        raise ValueError("客服 full_path suite 缺少完整的 KB/fixture/version 身份")

    from backend.evaluation.cs_fixture_catalog import (
        CS_EVAL_FIXTURE_SET,
        CS_EVAL_KB_ID,
        CS_EVAL_VERSION_ID,
        load_cs_fixture_catalog,
    )
    if (
        metadata["kb_id"] != CS_EVAL_KB_ID
        or metadata["fixture_set"] != CS_EVAL_FIXTURE_SET
        or metadata["dataset_version"] != "2.2"
    ):
        raise ValueError("客服 full_path suite 与锁定的 cs_eval_kb 范围不符")
    catalog = load_cs_fixture_catalog()
    if catalog.version_id != CS_EVAL_VERSION_ID:
        raise ValueError("客服 full_path fixture 版本未通过校验")

    # 预先构造 manager，runner 在每个用户轮次进入/退出以隔离 ContextVar。
    return {
        "kb_id": CS_EVAL_KB_ID,
        "fixture_set": CS_EVAL_FIXTURE_SET,
        "suite": str(metadata["suite"]),
        "dataset_version": str(metadata["dataset_version"]),
    }


def _source_ids(sources: object) -> list[str]:
    """从 SSE done.sources 提取安全的来源标识，不把标题/内容计作事实 ID。"""
    if not isinstance(sources, list):
        return []
    identifiers: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("fact_id", "source_id", "doc_id"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                identifiers.add(value.strip())
    return sorted(identifiers)
