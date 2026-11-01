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

from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner
from backend.shared.logger import logger

# cs_target（Router 预过滤产物）→ CS Graph expert 节点名
# 单一事实源在 customer_service/graph_state.py（cs_graph_node 同用此映射）
from backend.customer_service.graph_state import CS_TARGET_TO_EXPERT as _TARGET_TO_EXPERT
from backend.customer_service.graph_state import EXPERT_NAME_TO_NODE as _EXPERT_NAME_TO_NODE
from backend.customer_service.router.intents import FINE_INTENTS as _FINE_INTENTS

_VALID_TARGETS = set(_TARGET_TO_EXPERT)
_VALID_INTENTS = set(_FINE_INTENTS)
_VALID_NEXT_ACTION = {"answer", "clarify", "refuse", "handoff",
                      "propose", "propose_with_gap"}
_VALID_CS_ROUTE = {"hit", "miss_non_cs", "clarify_weak"}
_CS_EXPERT_NODES = {
    "cs_knowledge_expert", "cs_query_expert", "cs_action_expert",
    "cs_complaint_expert", "cs_handoff_expert",
}


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
    from backend.customer_service.graph_builder import get_cs_graph
    from backend.customer_service.graph_state import new_cs_graph_input
    from backend.customer_service.router.cs_router import get_cs_router
    from backend.config.customer_service import CS_GRAPH_RECURSION_LIMIT

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
            # thread_id 按 run+case 隔离，避免评测样本间与跨 run 状态串扰
            config = {
                "recursion_limit": CS_GRAPH_RECURSION_LIMIT,
                "configurable": {"thread_id": f"eval-cs-{run_token}-{case.id}"},
            }
            cs_input = new_cs_graph_input(
                user_message=case.question,
                user_id="eval_bot",
                # session 必须 case 级隔离：confirmation/handoff store 按
                # (user_id, session_id) 键控——session 仅 run 级时，同一 run
                # 内先跑 case 的动作提案 pending 态仍会拦住后续 case
                # （b0r3/r4/r5 三轮实测），而评测每条 case 即独立单轮会话
                session_id=f"cs_eval_{run_token}-{case.id}",
                conversation_id=f"eval-cs-{run_token}-{case.id}",
                cs_route=get_cs_router().route(case.question).model_dump(),
            )
            final_state = graph.invoke(cs_input, config=config)
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

    当前可断言信号（不依赖 P1 CSUnderstanding 契约）：
      - 域进入：cs_route=hit ⇒ status 流出现 cs_graph_node；
      - 文本断言：must_contain / must_not_contain / forbidden_facts；
      - 澄清：next_action=clarify ⇒ 流中出现 clarification 事件。
    intent/entities 精确断言待 P1 CSUnderstanding 契约落地后在 actual 接线
    （actual.pending 标注 p1-fields，不计 fail）。多轮 case 暂以末轮输入执行，
    逐轮会话重放待 runner v2.1。
    """
    from backend.orchestration.graph import MultiAgentSystem

    agent = MultiAgentSystem()
    results: list[EvalResult] = []
    for case in cases:
        t0 = time.time()
        exp = case.expected
        nodes: set[str] = set()
        deltas: list[str] = []
        clarify_seen = False
        try:
            for evt in agent.stream_events(
                case.question, f"eval-cs-v2-{case.id}", kb_id="default",
                user_id="eval_bot",
            ):
                name = evt.get("event")
                data = evt.get("data") or {}
                if name == "status" and data.get("node"):
                    nodes.add(data["node"])
                elif name == "clarification":
                    clarify_seen = True
                elif name == "delta" and isinstance(data.get("content"), str):
                    deltas.append(data["content"])
            answer = "".join(deltas)
            problems: list[str] = []
            metrics: dict[str, float | None] = {}
            if exp.get("cs_route") == "hit" and "cs_graph_node" not in nodes:
                problems.append(f"未进入客服域: nodes={sorted(nodes)}")
            for kw in exp.get("must_contain") or []:
                if kw and kw not in answer:
                    problems.append(f"must_contain 未命中: {kw!r}")
            for kw in (exp.get("must_not_contain") or []) + (exp.get("forbidden_facts") or []):
                if kw and kw in answer:
                    problems.append(f"禁词命中: {kw!r}")
            if exp.get("next_action") == "clarify" and not clarify_seen:
                problems.append("期望澄清但未出现 clarification 事件")
            metrics["cs_domain_entered"] = 1.0 if "cs_graph_node" in nodes else 0.0
            metrics["clarified"] = 1.0 if clarify_seen else 0.0
            results.append(EvalResult(
                case_id=case.id,
                module="cs",
                status="fail" if problems else "pass",
                expected=exp,
                actual={"nodes": sorted(nodes), "answer_head": answer[:200],
                        "pending": "p1-fields: intent/entities 断言待 CSUnderstanding"},
                metrics=metrics,
                duration_ms=int((time.time() - t0) * 1000),
                error_msg="; ".join(problems) or None,
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
