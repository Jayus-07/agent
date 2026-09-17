"""selection_decision REST API — 表单任务页入口（spec §8）

路由前缀: /selection-decision（经 next.config.js rewrite 由 /api 代理）。
POST /tasks 创建任务后立即返回 task_id，workflow 异步执行；
前端通过 GET /tasks 与 GET /tasks/{id} 轮询进度。
"""
import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.competitor.store import get_store
from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.selection_decision.store import get_selection_decision_store
from backend.shared.logger import logger

router = APIRouter(prefix="/selection-decision", tags=["选品决策"])

# 后台任务强引用集合（事件循环只持弱引用，防 GC 回收，bpo-88831）
_BACKGROUND_TASKS: set[asyncio.Task] = set()


class FinanceParams(BaseModel):
    sell_price: float = Field(..., gt=0, description="预期售价")
    unit_cost: float = Field(..., ge=0, description="单件采购成本")
    platform_fee_rate: float = Field(0.05, ge=0, lt=1)
    shipping_cost: float = Field(0.0, ge=0)
    marketing_cost: float = Field(0.0, ge=0)
    monthly_fixed_cost: float = Field(0.0, ge=0)
    # 有意比 finance._validate 的 [0,1] 收紧：100% 利润率门槛无实际意义
    min_margin_rate: float = Field(0.25, ge=0, lt=1)
    initial_inventory: int = Field(100, gt=0)
    buffer_rate: float = Field(0.15, ge=0, le=1)


class TaskRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=64, description="品类关键词")
    platforms: list[str] = Field(
        default_factory=lambda: ["jd", "taobao", "amazon"], min_length=1)
    finance: FinanceParams
    panel_size: int = Field(7, ge=1, le=7, description="评审团人数")


async def _run_task(task_id: str, inputs: dict) -> None:
    """后台执行 workflow；非 success 结果都兜底回写（不永久滞留 running）"""
    try:
        ctx = await WorkflowExecutor().run("selection_decision", inputs=inputs)
        # partial 当前不可达（executor 只产出 success/failed），但兜底覆盖非 success 全集
        if ctx.status != "success":
            get_selection_decision_store().update_result(
                task_id, status=ctx.status, error=ctx.error or "workflow 执行失败")
    except Exception as e:
        logger.error(f"[SelectionDecision:api] 任务 {task_id} 执行异常: {e}")
        get_selection_decision_store().update_result(
            task_id, status="failed", error=str(e)[:500])


@router.post("/tasks")
async def create_task(req: TaskRequest):
    """提交选品决策任务（异步执行）"""
    # watchlist 预校验（同步轻量：只查启用条目数，不查快照）：
    # 空则直接 400，避免创建注定失败的异步任务；有候选但无快照仍走异步失败路径。
    if not get_store().list_watch(enabled_only=True):
        raise HTTPException(
            status_code=400,
            detail="watchlist 为空：请先在竞品监控添加并启用候选商品 URL")
    store = get_selection_decision_store()
    inputs = {
        "category": req.category,
        "platforms": req.platforms,
        "finance": req.finance.model_dump(),
        "panel_size": req.panel_size,
    }
    task_id = store.create(inputs)
    inputs["task_id"] = task_id
    task = asyncio.create_task(_run_task(task_id, inputs))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    logger.info(f"[SelectionDecision:api] 任务已提交: {task_id} ({req.category})")
    return {"task_id": task_id, "status": "running"}


@router.get("/tasks")
def list_tasks(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return {"tasks": get_selection_decision_store().list(page=page, page_size=page_size)}


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    row = get_selection_decision_store().get(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return row


# ── B1：决策拍板 / 表现回填（2026-09-17 UX 会话补齐） ──────────────
# store 层 set_user_decision/set_feedback/record_decision 早已就绪但无路由，
# 前端也无入口（docs/未完成功能进度汇总 B1）。语义约定：
# - 拍板 = 留痕 + 用户决策一步完成（workflow 当前不自动 record_decision）；
# - adopted / rejected / deferred 与 store.set_user_decision docstring 同构；
# - 快照仅留拍板时点的证据/评分引用，事后表现走 feedback 端点回填。

VALID_USER_DECISIONS = ("adopted", "rejected", "deferred")


class DecisionRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, max_length=128)
    decision: str = Field(..., description="adopted / rejected / deferred")
    category: str | None = Field(None, max_length=64)
    recommendation: str | None = Field(None, max_length=64,
                                       description="workflow 建议，缺省记 manual")
    evidence_snapshot: dict = Field(default_factory=dict)
    score_snapshot: dict = Field(default_factory=dict)


class FeedbackRequest(BaseModel):
    actual_metrics: dict = Field(..., description="事后真实表现：销量/评价/收益等")


def _require_task(task_id: str) -> None:
    if get_selection_decision_store().get(task_id) is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")


@router.get("/tasks/{task_id}/decisions")
def list_task_decisions(task_id: str):
    """列出任务下全部决策留痕（拍板时间倒序）"""
    _require_task(task_id)
    return {"decisions": get_selection_decision_store().list_decisions_by_task(task_id)}


@router.post("/tasks/{task_id}/decisions", status_code=201)
def submit_decision(task_id: str, req: DecisionRequest):
    """拍板：创建决策留痕并回填用户决策（幂等由 decision_version 递增表达）"""
    if req.decision not in VALID_USER_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail=f"decision 必须是 {'/'.join(VALID_USER_DECISIONS)} 之一，收到: {req.decision}")
    _require_task(task_id)
    store = get_selection_decision_store()
    decision_id = store.record_decision(
        task_id=task_id,
        candidate_id=req.candidate_id,
        category=req.category,
        evidence_snapshot=req.evidence_snapshot,
        score_snapshot=req.score_snapshot,
        recommendation=req.recommendation or "manual",
    )
    store.set_user_decision(decision_id, req.decision)
    logger.info(f"[SelectionDecision:api] 拍板 {decision_id}: {req.decision} "
                f"(task={task_id}, candidate={req.candidate_id})")
    return store.get_decision(decision_id)


@router.post("/decisions/{decision_id}/feedback")
def submit_feedback(decision_id: str, req: FeedbackRequest):
    """表现回填：事后真实销量/评价/收益等，记 feedback_at"""
    store = get_selection_decision_store()
    if not store.set_feedback(decision_id, req.actual_metrics):
        raise HTTPException(status_code=404, detail=f"决策不存在: {decision_id}")
    logger.info(f"[SelectionDecision:api] 表现回填 {decision_id}")
    return store.get_decision(decision_id)
