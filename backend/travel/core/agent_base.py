"""travel/core/agent_base.py — BaseAgent 统一契约（v3 §2 / v4 §5 冻结）

Agent = LangGraph 域图节点 + 本契约（命名/输入输出契约/超时预算/降级
策略/遥测五要素），不是新框架。Phase 1 只立规格；experts/base.py 的
run_expert_safely（异常不穿透语义）在 Phase 3 升格接入本契约。子 Agent
互相禁止调用，数据传递只走共享 state；工具白名单见 tools/spec.py。
"""
from __future__ import annotations

from dataclasses import dataclass

# 7 个 Agent 的规范名（v3 冻结）。白名单与守护测试以此为准。
AGENT_SUPERVISOR = "supervisor"
AGENT_REQUIREMENT = "requirement"
AGENT_RESEARCH = "research"
AGENT_PLANNING = "planning"
AGENT_OPTIMIZATION = "optimization"
AGENT_COMMERCE = "commerce"
AGENT_ASSISTANT = "assistant"

CANONICAL_AGENTS: tuple[str, ...] = (
    AGENT_SUPERVISOR,
    AGENT_REQUIREMENT,
    AGENT_RESEARCH,
    AGENT_PLANNING,
    AGENT_OPTIMIZATION,
    AGENT_COMMERCE,
    AGENT_ASSISTANT,
)


@dataclass(frozen=True)
class AgentSpec:
    """单个 Agent 的治理规格（timeout_s=None 表示由上层预算约束）。"""

    name: str
    description: str
    timeout_s: float | None
    degradation: str


# 超时/降级口径 = v3 §3 职责边界表的代码化（冻结值）。
AGENT_SPECS: dict[str, AgentSpec] = {
    AGENT_SUPERVISOR: AgentSpec(
        name=AGENT_SUPERVISOR,
        description="域主 Agent：意图判定 + stage 推进 + 护栏 + deadline 检查",
        timeout_s=None,
        degradation="受整图 30s deadline 约束；超限强制 REPORT（已有成果+披露）",
    ),
    AGENT_REQUIREMENT: AgentSpec(
        name=AGENT_REQUIREMENT,
        description="自然语言 → TripBrief：两层抽取（规则兜底 + LLM 补槽）+ clarify 内置",
        timeout_s=10.0,
        degradation="LLM 失败回落规则层 + 追问（零重试）",
    ),
    AGENT_RESEARCH: AgentSpec(
        name=AGENT_RESEARCH,
        description="候选池构建 + 天气三态 + 知识/风险摘录，产出附加 Evidence",
        timeout_s=15.0,
        degradation="候选池空如实说明；天气 unavailable 继续规划；知识失败只留免责",
    ),
    AGENT_PLANNING: AgentSpec(
        name=AGENT_PLANNING,
        description="must_go 解析 + 日程骨架分配",
        timeout_s=10.0,
        degradation="失败 status=failed 不穿透，supervisor 兜底",
    ),
    AGENT_OPTIMIZATION: AgentSpec(
        name=AGENT_OPTIMIZATION,
        description="排程 + 2-opt 路径优化 + 坏天气换点 + 预算估算 + 版本盖章",
        timeout_s=15.0,
        degradation="失败不穿透，supervisor 输出骨架级成果 + 披露",
    ),
    AGENT_COMMERCE: AgentSpec(
        name=AGENT_COMMERCE,
        description="交易唯一入口：比价/深链 + 下单/取消/确认（kind 参数 + provider adapter）",
        timeout_s=None,
        degradation="fail-closed：UNKNOWN→IN_DOUBT 绝不猜成败（确认门/幂等账本约束）",
    ),
    AGENT_ASSISTANT: AgentSpec(
        name=AGENT_ASSISTANT,
        description="QUERY 行程问答：只引 state/RAG 事实，禁编造",
        timeout_s=8.0,
        degradation="失败→模板直答",
    ),
}
