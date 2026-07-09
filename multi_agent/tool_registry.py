"""
tool_registry.py — Capability 注册表

Capability 是 Planner 与 Skill 之间唯一的契约。
此注册表定义:
  - CAPABILITY_MAP:    capability → LangGraph 节点名（仅用于图路由）
  - CAPABILITY_SCHEMA: capability → 描述/参数（用于 Planner prompt 生成）

Skill 自己持有 Tool，Tool 调用 Infrastructure。
Planner → Capability → Skill → Tool → Infrastructure
"""

from typing import Dict, List, Optional
from utils.logger import logger


class ToolRegistry:
    """
    Capability 注册表。

    用法:
        registry = ToolRegistry()
        node = registry.get_node("sql.query")   # → "sql_skill"
        caps = registry.get_available_capabilities()
    """

    # capability → LangGraph 节点名（仅用于图路由）
    CAPABILITY_MAP: Dict[str, str] = {
        "sql.query":        "sql_skill",
        "rag.search":       "rag_skill",
        "report.generate":  "report_skill",
    }

    # capability → {description, params, 示例}（用于 Planner/Critique prompt）
    CAPABILITY_SCHEMA: Dict[str, dict] = {
        "sql.query": {
            "description": "查询 PostgreSQL 跨境电商数据库，返回 Markdown 表格。覆盖商品/订单/库存/广告/物流/客户等 15 张表。",
            "params": {"question": "自然语言查询问题（中文/英文）"},
            "示例": {"question": "查询Amazon US渠道最近7天的销售额和订单数"},
        },
        "rag.search": {
            "description": "从跨境电商知识库中检索 SOP/规范/FAQ/Listing指南等非结构化内容",
            "params": {"question": "检索问题"},
            "示例": {"question": "Amazon FBA发货的标准操作流程SOP"},
        },
        "report.generate": {
            "description": "生成结构化 Markdown 报告（含图表），基于数据库中的实时数据。必须有前序步骤提供数据后再调用。",
            "params": {
                "report_type": (
                    "报告类型，可选值：\n"
                    "  - daily_sales: 销售日报\n"
                    "  - product_performance: 商品动销分析\n"
                    "  - inventory_health: 库存健康报告\n"
                    "  - ad_performance: 广告效果分析\n"
                    "  - order_fulfillment: 订单履约报告\n"
                    "  - customer_analysis: 客户分析报告"
                ),
                "filters": "筛选条件字典，如 {'channel': 'Amazon'}",
            },
            "示例": {"report_type": "daily_sales", "filters": {"channel": "Amazon"}},
        },
    }

    def get_node(self, capability: str) -> Optional[str]:
        """根据 capability 获取对应的图节点名"""
        node = self.CAPABILITY_MAP.get(capability)
        if not node:
            logger.warning(f"[ToolRegistry] 未知 capability: {capability}")
        return node

    def get_worker(self, capability: str) -> Optional[str]:
        """向后兼容别名（同 get_node）"""
        return self.get_node(capability)

    def get_schema(self, capability: str) -> Optional[dict]:
        """获取 capability 的参数 schema"""
        return self.CAPABILITY_SCHEMA.get(capability)

    def get_available_capabilities(self) -> List[str]:
        """返回所有可用的 capability 列表"""
        return list(self.CAPABILITY_MAP.keys())

    def get_capabilities_description(self) -> str:
        """生成 Planner prompt 用的能力描述文本"""
        lines = []
        for cap_name, schema in self.CAPABILITY_SCHEMA.items():
            lines.append(f"  - {cap_name}: {schema['description']}")
        return "\n".join(lines)

    def get_capabilities_schema_text(self) -> str:
        """生成完整的 capability schema 文本，用于 Critique prompt"""
        import json
        lines = []
        for cap_name in self.get_available_capabilities():
            schema = self.get_schema(cap_name)
            if not schema:
                continue
            lines.append(f"### {cap_name}")
            lines.append(f"描述: {schema['description']}")
            lines.append(f"参数: {json.dumps(schema['params'], ensure_ascii=False)}")
            if "示例" in schema:
                lines.append(f"示例: {json.dumps(schema['示例'], ensure_ascii=False)}")
            lines.append("")
        return "\n".join(lines)


# 全局单例
tool_registry = ToolRegistry()
