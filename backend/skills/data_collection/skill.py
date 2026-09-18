"""skills/data_collection/skill.py — Data Collection Skill. Capability: data.collect"""
from backend.data_collection.tool import data_collection_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


class DataCollectionSkill(BaseSkill):
    """数据采集 Skill — 从外部数据源采集、清洗、分析并写入数据库"""

    name = "data_collection"
    capabilities = ["data.collect"]
    description = "从外部数据源采集电商业务数据（商品/订单/店铺/库存/供应商），经 Pandas 清洗分析后写入数据库。支持本地文件和 HTTP API 两种数据源。"
    params_schema = {
        "source": {"type": "string", "required": True,
                   "description": "数据源标识: static://datasets/products.json 或 http://localhost:8001/mock/products"},
        "target_table": {"type": "string", "required": False, "description": "目标数据库表名（默认 stg_products）"},
        "fetcher_type": {"type": "string", "required": False, "enum": ["static", "http"],
                         "description": "数据源类型（默认 static）"},
        "dedup_keys": {"type": "string", "required": False, "description": "去重键字段，逗号分隔（如 SKU,仓库）"},
        "idempotency_key": {"type": "string", "required": False,
                            "description": "客户端幂等键；重复提交同键不会重复写入"},
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
