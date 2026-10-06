"""Runtime 基础枚举，供 Registry 与 Router 契约共同依赖。"""

from enum import Enum

from pydantic import BaseModel, Field

class RuntimeType(str, Enum):
    """运行时家族，不绑定具体业务域。"""

    AGENT = "agent_runtime"
    WORKFLOW = "workflow_runtime"
    PLAN = "plan_runtime"
    DIRECT = "direct_runtime"
    GENERIC = "generic_runtime"


class RuntimeTarget(BaseModel):
    """具体 Runtime 目标，放在中立模块避免 Registry→router 包循环。"""

    type: RuntimeType
    id: str = Field(..., min_length=1)
    subflow: str | None = None
