"""app/api/routes/demo.py — Demo API

端点：
- POST /api/demo/seed              导入 demo 数据
- POST /api/demo/run/{scenario_id}  触发单个 demo 场景
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.seed.demo.runner import get_demo_runner
from backend.shared.logger import logger

router = APIRouter(prefix="/demo", tags=["Demo"])


@router.post("/seed")
async def seed_demo_data() -> dict[str, Any]:
    """导入所有 demo 数据（商品 + 销售 + 阈值 + 策略）"""
    try:
        runner = get_demo_runner()
        result = runner.seed_data()
        return {"ok": True, "result": result}
    except Exception as e:
        logger.warning(f"[Demo] seed 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run/{scenario_id}")
async def run_demo_scenario(scenario_id: str) -> dict[str, Any]:
    """触发单个 demo 场景

    scenario_id:
    - daily_report: 经营日报（Workflow）
    - inventory_alert: 库存预警（Workflow）
    - sales_anomaly: 销量异常分析（Agent /chat 跳转）
    - product_optimization: 商品优化建议（Agent /chat 跳转）
    """
    valid = {"daily_report", "inventory_alert", "sales_anomaly", "product_optimization"}
    if scenario_id not in valid:
        raise HTTPException(
            status_code=404,
            detail=f"未知场景: {scenario_id}，可选: {valid}"
        )

    try:
        runner = get_demo_runner()
        result = await runner.run_scenario(scenario_id)
        return {"ok": True, **result}
    except Exception as e:
        logger.warning(f"[Demo] run {scenario_id} 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
