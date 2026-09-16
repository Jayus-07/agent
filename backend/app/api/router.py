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
    prompts,
    cs_admin,
    evaluation,
    internal_ai,
    approvals,
    maps,
    tasks,
)
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.keyword_routes import router as keyword_router

api_router = APIRouter()

# ── 业务路由 ──────────────────────────────────
api_router.include_router(auth_local.router)  # 自建认证（2026-09-15 拆分，替代 Java auth-service）
api_router.include_router(auth_local.sys_router)  # 用户中心（register）
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
api_router.include_router(prompts.router)  # Prompt 管理
api_router.include_router(cs_admin.router)  # 客服会话管理
api_router.include_router(evaluation.router)  # 评测集管理
api_router.include_router(internal_ai.router)  # Java→Python 工具网关（X-Internal-Token 鉴权）
api_router.include_router(approvals.router)  # 写操作工具审批门（human-in-the-loop）
api_router.include_router(tasks.router)  # 异步任务编排（Celery + LangGraph checkpoint）
api_router.include_router(maps.router)  # 腾讯位置服务代理（前端调 /api/map/*，Key 不出后端）

# ── 系统路由 ──────────────────────────────────
api_router.include_router(health_router)


__all__ = ["api_router"]