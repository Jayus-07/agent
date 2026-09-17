"""travel/supervisor.py — 旅游域调度器（纯规则）

与主图 supervisor 定位一致：**不调 LLM 的确定性调度器**。
决策依据全部来自状态的事实（哪些专家跑过、校验过没过、修复了几轮），
没有任何需要「理解」的环节 —— 所以它不需要模型，也不该有模型，
否则同一份状态可能被调去不同的分支，链路就无法复现了。

阶段推进（stages）：
  slot → poi → transit → budget → risk → validate
         └────────── repair ←── 校验失败且未达轮数上限
  validate 通过 / 无法修复 / 轮数用尽 → report

四条终止护栏：
  1. TRAVEL_MAX_STEPS —— 全局步数上限（防未知循环）
  2. TRAVEL_MAX_REPAIR_ROUNDS —— 修复轮数上限（防 validate↔repair 乒乓）
  3. repair_stalled —— 修复器明确回报「本轮违反无自动修复手段」（防 repair 原地打转）
  4. 任一专家失败且无产物 —— 直接 report，把失败如实告知而不是空转重试

护栏的共同前提：**本节点只认状态里的事实，不认上游节点声明的 stage**。
任何「判定收尾」的分支都必须落一个可被 decide() 读到的事实，否则只是把
stage 写成 report 而路由照旧 —— repair 的无法修复分支就曾因此死循环。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from langgraph.types import Command

from backend.config import travel as T
from backend.shared.logger import logger
from backend.travel.graph_state import (
    EXPERT_TO_NODE,
    TRAVEL_BUDGET_EXPERT,
    TRAVEL_POI_EXPERT,
    TRAVEL_REPAIR,
    TRAVEL_REPORTER,
    TRAVEL_RISK_EXPERT,
    TRAVEL_SUPERVISOR,
    TRAVEL_TRANSIT_EXPERT,
    TRAVEL_VALIDATOR,
    load_brief,
    load_itinerary,
    load_validation,
)


class TravelStage(str, Enum):
    """域图内部阶段（也是埋点与 trace 标签的取值域）"""
    POI = "poi"
    TRANSIT = "transit"
    BUDGET = "budget"
    RISK = "risk"
    VALIDATE = "validate"
    REPAIR = "repair"
    REPORT = "report"
    DONE = "done"


_STAGE_TO_NODE: dict[TravelStage, str] = {
    TravelStage.POI: TRAVEL_POI_EXPERT,
    TravelStage.TRANSIT: TRAVEL_TRANSIT_EXPERT,
    TravelStage.BUDGET: TRAVEL_BUDGET_EXPERT,
    TravelStage.RISK: TRAVEL_RISK_EXPERT,
    TravelStage.VALIDATE: TRAVEL_VALIDATOR,
    TravelStage.REPAIR: TRAVEL_REPAIR,
    TravelStage.REPORT: TRAVEL_REPORTER,
    TravelStage.DONE: TRAVEL_REPORTER,
}

_STAGE_TO_EXPERT: dict[TravelStage, str] = {
    TravelStage.POI: "poi",
    TravelStage.TRANSIT: "transit",
    TravelStage.BUDGET: "budget",
    TravelStage.RISK: "risk",
}


@dataclass(frozen=True)
class TravelDecision:
    """调度决策（stage + 可读理由，理由进日志与 trace）"""
    stage: TravelStage
    reason: str


def _experts_done(state: dict) -> set[str]:
    return {e.get("expert", "") for e in state.get("expert_history", [])}


def decide(state: dict) -> TravelDecision:
    """纯函数决策：状态 → 下一步阶段。

    单测可以穷举各种状态组合，不需要构造 LangGraph 运行时 —— 这正是把
    调度做成纯函数的目的。
    """
    if state.get("finished"):
        return TravelDecision(TravelStage.DONE, "已结束")

    step_count = state.get("step_count") or 0
    if step_count >= T.TRAVEL_MAX_STEPS:
        return TravelDecision(
            TravelStage.REPORT,
            f"达到步数上限 {T.TRAVEL_MAX_STEPS}，强制收尾",
        )

    if state.get("brief_missing"):
        return TravelDecision(
            TravelStage.REPORT,
            f"必填槽位缺失 {state['brief_missing']}，转为追问",
        )

    done = _experts_done(state)

    if "poi" not in done:
        return TravelDecision(TravelStage.POI, "检索候选池并生成行程骨架")

    if not state.get("candidates"):
        return TravelDecision(TravelStage.REPORT, "候选池为空，无法规划")

    if "transit" not in done:
        return TravelDecision(TravelStage.TRANSIT, "骨架已就绪，排出时刻与通勤")

    if load_itinerary(state) is None:
        return TravelDecision(TravelStage.REPORT, "排程未产出行程，终止规划")

    if "budget" not in done:
        return TravelDecision(TravelStage.BUDGET, "行程已排定，核算费用")

    if "risk" not in done:
        return TravelDecision(TravelStage.RISK, "核算完成，标注数据来源与风险")

    report = load_validation(state)
    if report is None:
        return TravelDecision(TravelStage.VALIDATE, "执行硬约束校验")

    if report.passed:
        return TravelDecision(
            TravelStage.REPORT,
            f"校验通过（提示 {len(report.warnings)} 项）",
        )

    # 修复器已明确回报「本轮违反无自动修复手段」——再调一次结果必然相同，
    # 直接收尾交由 reporter 如实披露（顺序在轮数判定之前：这种情况往往是
    # 第 1 轮就卡住，轮数还是 0，按轮数判会误判回 REPAIR）。
    if state.get("repair_stalled"):
        return TravelDecision(
            TravelStage.REPORT,
            f"存在 {len(report.errors)} 项违反且无自动修复手段，如实披露",
        )

    repair_rounds = state.get("repair_rounds", 0)
    if repair_rounds >= T.TRAVEL_MAX_REPAIR_ROUNDS:
        return TravelDecision(
            TravelStage.REPORT,
            f"存在 {len(report.errors)} 项违反且修复轮数已达上限 "
            f"{T.TRAVEL_MAX_REPAIR_ROUNDS}，如实披露",
        )

    return TravelDecision(
        TravelStage.REPAIR,
        f"存在 {len(report.errors)} 项约束违反，执行第 {repair_rounds + 1} 轮局部修复",
    )


def stage_targets() -> dict[str, str]:
    """阶段 → 目标节点名的公开映射。

    对外暴露是为了让结构回归测试能断言「每个跳转目标都是真实存在的节点」：
    supervisor 用 Command(goto=...) 动态路由，LangGraph 的静态边列表**不体现**
    这些目标（客服域同样如此），因此目标名写错或节点被改名时静态检查发现不了，
    只能等运行期炸。这个映射就是那道提前拦截。
    """
    return {stage.value: node for stage, node in _STAGE_TO_NODE.items()}


def travel_supervisor_node(state: dict) -> Command:
    """调度节点：返回 Command(goto=...) 驱动条件跳转。"""
    decision = decide(state)
    target = _STAGE_TO_NODE[decision.stage]
    step_count = (state.get("step_count") or 0) + 1

    logger.info(
        "[TravelSupervisor] stage=%s → node=%s step=%d reason=%s",
        decision.stage.value, target, step_count, decision.reason,
    )

    # 版本事实（任务书 §4/§5）：决策可复现的前提是「产物属于当前需求」。
    # brief 与 itinerary 的版本号随决策落 trace —— 版本错位（不应发生，
    # planning_reset 在槽位层已兜住）能在 trace 里一眼定位，而不是靠猜。
    itinerary = load_itinerary(state)
    brief = load_brief(state)
    version_facts = {
        "brief_version": brief.version,
        "plan_version": itinerary.plan_version if itinerary else None,
        "plan_status": itinerary.status if itinerary else None,
    }

    return Command(
        goto=target,
        update={
            "stage": decision.stage.value,
            "supervisor_decision": {
                "stage": decision.stage.value,
                "reason": decision.reason,
                "step": step_count,
                "layer": "rule",
                **version_facts,
            },
            "step_count": step_count,
            "current_expert": _STAGE_TO_EXPERT.get(decision.stage, ""),
            "finished": decision.stage in (TravelStage.REPORT, TravelStage.DONE),
        },
    )


__all__ = [
    "TravelStage",
    "TravelDecision",
    "travel_supervisor_node",
    "stage_targets",
    "decide",
    "EXPERT_TO_NODE",
    "TRAVEL_SUPERVISOR",
]
