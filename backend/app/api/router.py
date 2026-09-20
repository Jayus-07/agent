"""app/api/router.py — 路由聚合

集中注册所有 API 路由，避免 server.py 直接 import 每个 router。
新增路由时只需在此处添加一行 include_router。
"""
from fastapi import APIRouter

from backend.app.api.routes import (
    auth_local,
    chat,
    sql,
    rag,
    report,
    llm,
    observability,
    memory,
    data,
    mcp,
    agents,
    capabilities,
    workflows,
    inventory_alerts,
    demo,
    reports,
    schedules,
    feedback,
    competitor,
    selection,
    selection_decision,
    selection_funnel,
    prompts,
    cs_admin,
    cs_agent_ws,
    evaluation,
    internal_ai,
    approvals,
    budgets,
    model_prices,
    idempotency,
    maps,
    tasks,
    admin_tasks,
    sys_config_admin,
    sys_providers,
    sys_model_roles,
    model_config,
)
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.keyword_routes import router as keyword_router

api_router = APIRouter()

# ── 业务路由 ──────────────────────────────────
api_router.include_router(auth_local.router)  # 自建认证（2026-09-15 拆分，替代 Java auth-service）
api_router.include_router(auth_local.sys_router)  # 用户中心（register）
api_router.include_router(sys_config_admin.router)  # 灰度开关动态配置（2026-09-16 Lite，管理员闸）
api_router.include_router(sys_providers.router)  # 模型供应商清单与分级探测
api_router.include_router(sys_model_roles.router)  # 模型角色生效视图
api_router.include_router(model_config.router)  # 模型配置写入、历史与漂移
api_router.include_router(chat.router)
api_router.include_router(sql.router)
api_router.include_router(rag.router)
api_router.include_router(keyword_router)
api_router.include_router(report.router)
api_router.include_router(llm.router)
api_router.include_router(observability.router)
api_router.include_router(memory.router)
api_router.include_router(data.router)
api_router.include_router(data.assets_router)
api_router.include_router(data.pipeline_router)
api_router.include_router(mcp.router)
api_router.include_router(agents.router)  # Agent 层只读总览（B13 管理端 /agents）
api_router.include_router(capabilities.router)  # Capability 清单只读对账（B13 管理端 /skills）
api_router.include_router(workflows.router)
api_router.include_router(inventory_alerts.router)
api_router.include_router(demo.router)
api_router.include_router(reports.router)
api_router.include_router(schedules.router)
api_router.include_router(feedback.router)  # 2026-08-11 P1 反馈循环
api_router.include_router(competitor.router)  # 竞品监控
api_router.include_router(selection.router)  # 智能选品
api_router.include_router(selection_decision.router)  # 选品决策
api_router.include_router(selection_funnel.router)  # 智能选品漏斗（导入通道 2026-09-17）
api_router.include_router(prompts.router)  # Prompt 管理
api_router.include_router(cs_admin.router)  # 客服会话管理
api_router.include_router(cs_admin.confirm_router)  # P3.1: 确认卡片端点
api_router.include_router(cs_agent_ws.router)  # 坐席 WS 实时推送（ticket 鉴权，不走 X-API-Key）
api_router.include_router(evaluation.router)  # 评测集管理
api_router.include_router(internal_ai.router)  # Java→Python 工具网关（X-Internal-Token 鉴权）
api_router.include_router(approvals.router)  # 写操作工具审批门（human-in-the-loop）
api_router.include_router(budgets.router)  # 用户预算与管理员预算治理
api_router.include_router(model_prices.router)  # 模型价格版本与双人审核治理
api_router.include_router(idempotency.router)  # 原操作者幂等状态查询
api_router.include_router(tasks.router)  # 异步任务编排（Celery + LangGraph checkpoint）
api_router.include_router(admin_tasks.router)  # 管理端任务中心（管理员闸 + 操作审计）
api_router.include_router(maps.router)  # 腾讯位置服务代理（前端调 /api/map/*，Key 不出后端）

# ── 系统路由 ──────────────────────────────────
api_router.include_router(health_router)


__all__ = ["api_router"]
