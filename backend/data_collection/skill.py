"""
data_collection/skill.py — DataCollectionSkill

遵循项目 BaseSkill 规范:
  - capabilities: ["data.collect"]
  - _tool_fn → data_collection_tool
  - execute() 由 BaseSkill 提供（带重试/超时/告警）
"""

from backend.orchestration.skills.base import BaseSkill
from backend.data_collection.tool import data_collection_tool
from backend.shared.logger import logger


class DataCollectionSkill(BaseSkill):
    """数据采集 Skill — 从外部数据源采集、清洗、分析并写入数据库"""

    name = "data_collection"
    capabilities = ["data.collect"]
    description = "从外部数据源采集电商业务数据（商品/订单/店铺/库存/供应商），经 Pandas 清洗分析后写入数据库。支持本地文件和 HTTP API 两种数据源。"
    params_schema = {
        "source": "数据源标识: static://datasets/products.json 或 http://localhost:8001/mock/products",
        "target_table": "目标数据库表名（默认 stg_products）",
        "fetcher_type": "static | http",
        "dedup_keys": "去重键字段，逗号分隔（如 SKU,仓库）",
    }
    examples = [{"source": "static://datasets/products.json", "target_table": "stg_products", "fetcher_type": "static"}]

    @property
    def _tool_fn(self):
        return data_collection_tool


async def data_collection_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器 — 由 Supervisor 路由到此节点"""
    skill = DataCollectionSkill()
    cap = (
        state.get("plan", {})
        .get("nodes", {})
        .get(state.get("current_step_id", ""), {})
        .get("capability", "data.collect")
    )
    logger.info(f"[DataCollection Skill] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
