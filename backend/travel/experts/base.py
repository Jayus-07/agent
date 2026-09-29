"""travel/experts/base.py — 专家基础设施

复用 CS 域的专家契约形态（ExpertResult + run_expert_safely 安全包装），
但刻意不复用其实现：CS 的 ExpertResult 绑定了 response_draft / evidence 等
客服语义字段，旅游专家之间传递的是结构化 POI/行程对象，硬套会造成
「字段名对不上、只能塞进 data 里当黑盒」的假复用。

STOP F（2026-09-29）：执行生命周期内部收敛到 core/node_runtime；公开契约
（签名/TravelExpertResult 字段/status 枚举）与遥测形态（travel_expert_{name}
span 软失败 + start/done 日志，经 TravelExpertHooks 原样迁移）逐字节冻结
不变。旅游专家是纯规则快路径：无 timeout 参数、无领域 metrics。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable, TypedDict

from backend.core.node_runtime import (
    ErrorPolicy,
    ExecutionContext,
    NodeRunner,
    TravelExpertHooks,
)


class TravelExpertStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class TravelExpertType(str, Enum):
    """规则专家（全部为业务编排，不含 LLM 决策；weather 为 2026-09-22 新增）"""
    POI = "poi"
    TRANSIT = "transit"
    WEATHER = "weather"
    BUDGET = "budget"
    RISK = "risk"


class TravelExpertResult(TypedDict, total=False):
    """专家统一输出契约

    与 CS 的 ExpertResult 的差异：去掉客服语义字段，改为
    data（结构化产物）+ notes（给人看的提示）两个通用出口。
    """
    expert: str
    status: str
    data: dict
    notes: list[str]
    error: str
    duration_ms: int


def run_expert_safely(
    expert_name: str,
    fn: Callable[..., dict],
    state: dict[str, Any],
) -> TravelExpertResult:
    """安全执行专家，异常不穿透（与 CS 同语义）。

    单个专家失败不应让整条旅游链路崩掉：返回 status=failed 的结果，
    由 supervisor 决定是跳过还是终止 —— 决策权在调度器，不在专家。

    Phase 4（任务书 §11）：每个专家调用统一建 span —— 此前专家只有
    duration_ms 日志，与 validator（每轴独立 span）不一致，专家延迟与
    失败率在 trace 里不可见。软失败：无活跃 trace 时为 noop span。
    （span 生命周期自 STOP F 起在 core/node_runtime 的 TravelExpertHooks
    内，命名与形态不变。）
    """
    ctx = ExecutionContext(node_name=expert_name, domain="travel")

    # 包装放在 fn 侧而非 runner 返回之后（与旧实现同构）：包装阶段异常
    # （如 fn 返回非 dict）按专家失败处理（status=failed），不穿透。
    def _invoke(s: dict[str, Any]) -> dict:
        result = fn(s)
        result.setdefault("expert", expert_name)
        result.setdefault("status", TravelExpertStatus.SUCCESS.value)
        return result

    node_result = NodeRunner().run(
        ctx,
        _invoke,
        state,
        policy=ErrorPolicy.SWALLOW_TO_STATUS,
        hooks=TravelExpertHooks(),
    )

    if node_result.status == TravelExpertStatus.FAILED.value:
        return TravelExpertResult(
            expert=expert_name,
            status=TravelExpertStatus.FAILED.value,
            data={},
            notes=[],
            error=node_result.error or "",
            duration_ms=node_result.duration_ms,
        )

    result = node_result.data
    result["duration_ms"] = node_result.duration_ms
    return result
