"""客服复合任务的有限计划契约与纯条件求值。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ShippingStatus = Literal[
    "not_shipped", "shipped", "delivered", "cancelled", "unknown",
]
TaskCapability = Literal[
    "query_logistics", "query_order_status", "propose_refund",
]
TaskStatus = Literal["success", "needs_clarification", "skipped", "failed"]


class CSTaskCondition(BaseModel):
    """只允许基于物流状态做等值判断。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact: Literal["shipping_status"]
    operator: Literal["eq"]
    value: ShippingStatus


class CSTask(BaseModel):
    """单个已登记客服能力；模型不能注入参数或业务 ID。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,15}$")
    capability: TaskCapability
    depends_on: list[str] = Field(default_factory=list, max_length=3)
    condition: CSTaskCondition | None = None


class CSTaskPlan(BaseModel):
    """最多三步的有向无环客服任务计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    primary_intent: Literal["t_logistics", "t_order_status"]
    tasks: list[CSTask] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_graph(self) -> "CSTaskPlan":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id 必须唯一")
        known: set[str] = set()
        for task in self.tasks:
            if len(task.depends_on) != len(set(task.depends_on)):
                raise ValueError("depends_on 不得重复")
            if any(dep not in task_ids for dep in task.depends_on):
                raise ValueError("依赖必须指向计划内任务")
            if any(dep not in known for dep in task.depends_on):
                raise ValueError("任务顺序必须满足依赖先行")
            if task.condition is not None and not task.depends_on:
                raise ValueError("条件任务必须依赖事实查询任务")
            known.add(task.task_id)

        writes = [task for task in self.tasks if task.capability == "propose_refund"]
        if len(writes) > 1:
            raise ValueError("计划最多包含一个退款提案")
        if writes:
            write = writes[0]
            read_dependencies = {
                task.task_id for task in self.tasks
                if task.capability in ("query_logistics", "query_order_status")
            }
            if not write.condition or not write.depends_on:
                raise ValueError("退款提案必须有条件并依赖事实查询")
            if not (set(write.depends_on) & read_dependencies):
                raise ValueError("退款提案必须依赖订单或物流查询")
            if write.condition.fact == "shipping_status" and not any(
                task.task_id in write.depends_on
                and task.capability == "query_logistics"
                for task in self.tasks
            ):
                raise ValueError("物流状态条件必须依赖物流查询")
        return self


class CSTaskResult(BaseModel):
    """专家执行产物；facts 只含有限业务事实，不接受模型控制字段。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: TaskStatus
    facts: dict[str, Any] = Field(default_factory=dict)
    source: Literal[
        "sandbox_logistics_service", "business_logistics_service",
        "sandbox_order_service", "business_order_service",
        "sandbox_business_service", "unknown",
    ]
    error_type: Literal[
        "timeout", "network_error", "permission_denied", "validation_error",
        "business_error", "contract_error", "provider_error",
    ] | None = None

    @model_validator(mode="after")
    def validate_facts(self) -> "CSTaskResult":
        allowed = {
            "shipping_status", "order_count", "order_id", "order_no",
            "order_status",
        }
        if set(self.facts) - allowed:
            raise ValueError("facts 含未登记字段")
        if "shipping_status" in self.facts and self.facts["shipping_status"] not in {
            "not_shipped", "shipped", "delivered", "cancelled", "unknown",
        }:
            raise ValueError("shipping_status 不在白名单")
        if "order_status" in self.facts and self.facts["order_status"] not in {
            "pending", "paid", "shipped", "completed", "cancelled", "unknown",
        }:
            raise ValueError("order_status 不在白名单")
        if "order_count" in self.facts and (
            isinstance(self.facts["order_count"], bool)
            or not isinstance(self.facts["order_count"], int)
            or self.facts["order_count"] < 0
        ):
            raise ValueError("order_count 必须为非负整数")
        for key in ("order_id", "order_no", "order_status"):
            if key in self.facts and not isinstance(self.facts[key], str):
                raise ValueError(f"{key} 必须为文本")
        return self


def evaluate_task_condition(
    condition: CSTaskCondition | dict[str, Any], facts: dict[str, Any],
) -> bool:
    """对有限条件做 fail-closed 求值；缺失、未知或非法事实均为 False。"""
    try:
        if not isinstance(facts, dict):
            return False
        parsed = (
            condition if isinstance(condition, CSTaskCondition)
            else CSTaskCondition.model_validate(condition)
        )
    except Exception:
        return False
    value = facts.get(parsed.fact)
    if value in (None, "unknown"):
        return False
    return value == parsed.value


def build_rule_task_plan(user_message: str) -> dict[str, Any] | None:
    """识别可安全编排的显式「查物流，未发货则申请退款」请求。"""
    import re

    text = (user_message or "").strip()
    has_logistics = any(token in text for token in ("物流", "快递", "发货"))
    has_if = any(token in text for token in ("如果", "要是", "若", "假如"))
    has_not_shipped = any(
        token in text for token in (
            "没发货", "未发货", "还没发货", "还没有发货", "尚未发货",
        )
    )
    explicit_refund = bool(re.search(
        r"(?:申请|帮我|我要|给我|帮忙)(?:.{0,3})退款|退款申请", text,
    ))
    refund_negation = bool(re.search(
        r"(?:没有打算|没打算|没有必要|没必要|不考虑|不打算|"
        r"没有想|没想|不想|并不|不愿|不要|别|不需要|无需|不用|不必|"
        r"暂不|先别|先不|不)"
        r".{0,6}退款",
        text,
    ))
    refund_withdrawal = bool(re.search(
        r"(?:算了(?:吧)?|不用了|不需要了|不要了|不办了|"
        r"不想(?:了|办了|申请了|退了)|反悔(?:了)?|"
        r"改.{0,1}(?:主意|想法)|改变主意(?:了)?|"
        r"收回.{0,4}(?:申请|退款)?|放弃(?:了)?|"
        r"取消.{0,6}(?:退款|申请)?|撤销.{0,6}(?:退款|申请)?|"
        r"撤回.{0,6}(?:退款|申请)?)",
        text,
    ))
    if refund_negation or refund_withdrawal or not (
        has_logistics and has_if and has_not_shipped and explicit_refund
    ):
        return None
    return {
        "schema_version": 1,
        "primary_intent": "t_logistics",
        "tasks": [
            {"task_id": "q1", "capability": "query_logistics", "depends_on": []},
            {
                "task_id": "a1", "capability": "propose_refund",
                "depends_on": ["q1"],
                "condition": {
                    "fact": "shipping_status", "operator": "eq",
                    "value": "not_shipped",
                },
            },
        ],
    }
