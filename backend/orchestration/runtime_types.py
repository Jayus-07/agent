"""Runtime 基础枚举，供 Registry 与 Router 契约共同依赖。"""

from enum import Enum


class RuntimeType(str, Enum):
    """运行时家族，不绑定具体业务域。"""

    AGENT = "agent_runtime"
    WORKFLOW = "workflow_runtime"
    PLAN = "plan_runtime"
    DIRECT = "direct_runtime"
    GENERIC = "generic_runtime"
