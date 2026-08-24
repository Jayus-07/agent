"""selection_decision REST API — 表单任务页入口（spec §8）

路由前缀: /selection-decision（经 next.config.js rewrite 由 /api 代理）。
POST /tasks 创建任务后立即返回 task_id，workflow 异步执行；
前端通过 GET /tasks 与 GET /tasks/{id} 轮询进度。
"""
import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

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
    """后台执行 workflow；异常/失败时把任务标记为 failed"""
    try:
        ctx = await WorkflowExecutor().run("selection_decision", inputs=inputs)
        if ctx.status == "failed":
            get_selection_decision_store().update_result(
                task_id, status="failed", error=ctx.error or "workflow 执行失败")
    except Exception as e:
        logger.error(f"[SelectionDecision:api] 任务 {task_id} 执行异常: {e}")
        get_selection_decision_store().update_result(
            task_id, status="failed", error=str(e)[:500])


@router.post("/tasks")
async def create_task(req: TaskRequest):
    """提交选品决策任务（异步执行）"""
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
