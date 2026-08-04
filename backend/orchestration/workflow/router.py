"""workflow/router.py — Task Router

设计原则（按企业方案）：
- 业务对象 + 动作 + WorkflowMatch 三层加权
- Total Score = 0.3 * Object + 0.2 * Action + 0.5 * WorkflowMatch
- 规则 confidence < 0.7 → LLM 兜底（暂留接口，TODO）
- Router Index 由 WorkflowRegistry 自动生成（不硬编码业务对象词典）

输出 RouteResult：
- intent: str  （workflow 名 或 'agent_query'）
- candidate_mode: list[str]  （['workflow'] 或 ['agent'] 或 ['agent', 'workflow']）
- workflow_candidate: str | None
- confidence: float
- reasoning: str  （debug 用）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.orchestration.workflow.registry import (
    WorkflowRegistry,
    RouterEntry,
    get_workflow_registry,
)
from backend.shared.logger import logger


@dataclass
class RouteResult:
    """Task Router 输出"""
    intent: str                              # 'workflow:daily_report' / 'agent_query' / ...
    candidate_mode: list[str] = field(default_factory=list)   # ['workflow'] / ['agent']
    workflow_candidate: str | None = None    # 候选 workflow 名
    confidence: float = 0.0                  # 0~1
    reasoning: str = ""                      # debug 信息

    @property
    def is_workflow(self) -> bool:
        return "workflow" in self.candidate_mode

    @property
    def is_agent(self) -> bool:
        return "agent" in self.candidate_mode


class TaskRouter:
    """Task Router — 判断用户请求走 Workflow 还是 Agent

    用法：
        router = TaskRouter()
        result = await router.route("帮我生成今天的经营日报")
        if result.is_workflow:
            await executor.run(result.workflow_candidate)
        else:
            await pipeline.run(user_input)  # Agent
    """

    # 评分权重（按企业方案）
    W_OBJECT = 0.3
    W_ACTION = 0.2
    W_WORKFLOW_MATCH = 0.5

    # 规则层阈值：score >= WORKFLOW_THRESHOLD 走 workflow
    WORKFLOW_THRESHOLD = 0.3
    # LLM 二次确认阈值：score 在 [LLM_JUDGE_THRESHOLD, WORKFLOW_THRESHOLD) 之间
    LLM_JUDGE_THRESHOLD = 0.15

    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        embedding_client: Any | None = None,
        llm_client: Any | None = None,
    ):
        self.registry = registry or get_workflow_registry()
        self.embedding_client = embedding_client
        self.llm_client = llm_client
        logger.info(
            f"[TaskRouter] 初始化: {len(self.registry.router_index)} 个 workflow 在 Index"
        )

    async def route(self, user_query: str) -> RouteResult:
        """判断用户请求走 Workflow 还是 Agent

        决策逻辑：
        - 规则层 score >= 0.3 → 走 workflow（高置信）
        - 规则层 score < 0.3 但 >= 0.15 → 调 LLM 二次判断（TODO Phase 5）
        - 规则层 score < 0.15 → 直接走 agent（规则无匹配）

        Returns:
            RouteResult: 含 candidate_mode / workflow_candidate / confidence
        """
        # 1. 规则层
        rule_result = self._rule_match(user_query)
        logger.debug(
            f"[TaskRouter] 规则层结果: {rule_result.workflow_candidate} "
            f"confidence={rule_result.confidence:.3f}"
        )

        # 2. 高置信 → 直接采用
        if rule_result.confidence >= self.WORKFLOW_THRESHOLD:
            return rule_result

        # 3. 中等置信 → LLM 兜底（Phase 5 接入）
        if rule_result.confidence >= self.LLM_JUDGE_THRESHOLD and self.llm_client:
            logger.debug(
                f"[TaskRouter] 规则 confidence {rule_result.confidence:.3f} "
                f"in [0.15, 0.3), 调 LLM 二次判断"
            )
            # TODO Phase 5: 调 LLM 判断 workflow.trigger capability
            return self._llm_fallback(user_query, rule_result)

        # 4. 低置信 → 直接 agent
        return RouteResult(
            intent="agent_query",
            candidate_mode=["agent"],
            workflow_candidate=None,
            confidence=max(rule_result.confidence, 0.2),
            reasoning=(
                f"rule confidence {rule_result.confidence:.3f} < "
                f"{self.LLM_JUDGE_THRESHOLD}, fallback to agent"
            ),
        )

    def _rule_match(self, user_query: str) -> RouteResult:
        """规则层：业务对象 + 动作 + workflow_match 加权评分

        对每个 workflow 计算综合分，取最高分作为候选。
        """
        if not self.registry.router_index:
            # 没有注册的 workflow → 直接判为 agent
            return RouteResult(
                intent="agent_query",
                candidate_mode=["agent"],
                confidence=0.5,
                reasoning="no workflows registered",
            )

        # 1. 对每个 workflow 计算分数
        best: tuple[str, float, dict] | None = None
        scores: dict[str, dict] = {}
        for name, entry in self.registry.router_index.items():
            obj_score = entry.object_match_score(user_query)
            act_score = entry.action_match_score(user_query)
            # workflow_match = (object 或 action 任一命中即加分)
            workflow_match = max(obj_score, act_score)

            total = (
                self.W_OBJECT * obj_score
                + self.W_ACTION * act_score
                + self.W_WORKFLOW_MATCH * workflow_match
            )
            scores[name] = {
                "object": obj_score,
                "action": act_score,
                "workflow_match": workflow_match,
                "total": total,
            }
            if best is None or total > best[1]:
                best = (name, total, scores[name])

        # 2. 阈值判断
        if best is None or best[1] < 0.3:
            return RouteResult(
                intent="agent_query",
                candidate_mode=["agent"],
                confidence=0.4,
                reasoning=f"low workflow score ({best[1] if best else 0:.3f}), default to agent",
            )

        name, score, breakdown = best
        return RouteResult(
            intent=f"workflow:{name}",
            candidate_mode=["workflow"],
            workflow_candidate=name,
            confidence=min(score, 1.0),
            reasoning=(
                f"best match: {name} (score={score:.3f}, "
                f"object={breakdown['object']:.2f}, "
                f"action={breakdown['action']:.2f}, "
                f"workflow_match={breakdown['workflow_match']:.2f})"
            ),
        )

    def _llm_fallback(self, user_query: str, rule_result: RouteResult) -> RouteResult:
        """LLM 兜底（Phase 5 接入）

        当前实现：
        - 如果规则层 workflow_candidate 存在但 confidence 低 → 不采用，判为 agent
        - TODO: 调 LLM 判断是否调用 workflow.trigger capability（混合场景）
        """
        # 简单实现：规则层不通过 → agent
        return RouteResult(
            intent="agent_query",
            candidate_mode=["agent"],
            workflow_candidate=None,
            confidence=0.5,
            reasoning=(
                f"rule confidence {rule_result.confidence:.3f} < threshold, "
                f"fallback to agent (LLM integration TODO)"
            ),
        )