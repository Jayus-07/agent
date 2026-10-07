"""security/service_identity.py — 定时工作流服务身份（2026-10-08 #10）

为什么存在：APScheduler 触发的系统工作流（inventory_alert/daily_report）
在线程里执行时 request_context 四个身份 contextvar 全空 → SQLPolicyGuard
权限门（无 sql.read）与 G-20 空身份 fail-closed 连环拒绝——库存告警工作流
在 ECS 上因此「failed (0 steps)」（trace 实测最后一个 span = sql.guard
SQL_PERMISSION_DENIED）。

方案 A（2026-10-08 拍板）：调度入口绑**显式服务身份**，守卫零改动——
六层 SQL 安全（只读校验/表白名单/敏感列/函数白名单/LIMIT/readonly 连接
角色）全部保留，只是身份合法化。角色 service_workflow 只含 sql.read、
数据范围 all；G-20 的 fail-closed 语义不被绕过——身份不是缺失，
是显式的机器主体（svc: 前缀）。

边界：**只在 scheduler._run_async 绑定**。共享 WorkflowExecutor 同时服务
主图 workflow_executor（用户请求），绝不能在执行器层覆盖用户身份；手动
触发（POST /api/demo/run/*）保留调用者真人身份，审计更真实。
"""
from __future__ import annotations

from contextlib import contextmanager

from backend.core.request_context import (
    get_tool_department,
    get_tool_roles,
    get_tool_tenant_id,
    get_tool_user_id,
    set_tool_department,
    set_tool_roles,
    set_tool_tenant_id,
    set_tool_user_id,
)

#: 服务主体角色（security/authorization.py::ROLE_PERMISSION_CODES 登记，
#: 只含 sql.read；scope=all 见同处 ROLE_DATA_SCOPE 注释）
SERVICE_WORKFLOW_ROLE = "service_workflow"
SERVICE_TENANT_ID = "default"
SERVICE_DEPARTMENT = "system"


def service_user_id(workflow_name: str) -> str:
    """服务主体用户标识（审计归因键；svc: 前缀 = 机器流量）。"""
    return f"svc:workflow:{workflow_name}"


@contextmanager
def bind_service_identity(workflow_name: str):
    """把服务身份绑进 tool 上下文（contextvars），退出精确还原前值。

    捕获并还原前值而非清空：不假设外层为空——手动触发链路若在同线程
    先绑过用户身份，退出后必须回到用户身份而不是空（asyncio task 上下文
    本就按任务隔离，这里是线程复用场景的兜底）。
    """
    prev_user = get_tool_user_id()
    prev_tenant = get_tool_tenant_id()
    prev_roles = get_tool_roles()
    prev_department = get_tool_department()
    set_tool_user_id(service_user_id(workflow_name))
    set_tool_tenant_id(SERVICE_TENANT_ID)
    set_tool_roles((SERVICE_WORKFLOW_ROLE,))
    set_tool_department(SERVICE_DEPARTMENT)
    try:
        yield
    finally:
        set_tool_user_id(prev_user)
        set_tool_tenant_id(prev_tenant)
        set_tool_roles(prev_roles)
        set_tool_department(prev_department)


__all__ = [
    "SERVICE_WORKFLOW_ROLE",
    "SERVICE_TENANT_ID",
    "SERVICE_DEPARTMENT",
    "service_user_id",
    "bind_service_identity",
]
