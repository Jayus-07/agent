"""
tool_registry.py — Tool Registry (capability → worker 映射)

Planner 只输出 capability，不指定具体 tool。
Supervisor 通过此注册表找到对应 Worker 节点名。

如需新增能力，只需在此文件中添加映射即可。
"""

from typing import Dict, List, Optional
from utils.logger import logger


class ToolRegistry:
    """
    能力注册表：capability → worker_node_name 映射。

    用法:
        registry = ToolRegistry()
        worker = registry.get_worker("query_database")   # → "sql_worker"
    """

    # capability → worker 节点名
    CAPABILITY_MAP: Dict[str, str] = {
        "query_database":   "sql_worker",
        "search_knowledge": "rag_worker",
        "generate_report":  "report_worker",
    }

    # 每个 capability 的描述和参数 schema（用于 Planner prompt）
    CAPABILITY_SCHEMA: Dict[str, dict] = {
        "query_database": {
            "description": "查询 PostgreSQL 数据库中的结构化数据，返回 Markdown 表格",
            "params": {
                "question": "自然语言查询问题（中文/英文）",
            },
            "示例": {
                "question": "查询技术部所有项目的预算总额",
            },
        },
        "search_knowledge": {
            "description": "从知识库中检索文档、经验、最佳实践等非结构化内容",
            "params": {
                "question": "检索问题",
            },
            "示例": {
                "question": "技术部门预算管理的最佳实践",
            },
        },
        "generate_report": {
            "description": "生成结构化 Markdown 报告（含图表），基于数据库中的实时数据。必须有前序步骤提供数据后再调用。",
            "params": {
                "report_type": "报告类型: dept_summary(部门综合分析) / project_progress(项目进度) / demo_dept_summary(演示用)",
                "filters": "筛选条件字典，如 {'dept': '技术部'}",
            },
            "示例": {
                "report_type": "dept_summary",
                "filters": {"dept": "技术部"},
            },
        },
    }

    def get_worker(self, capability: str) -> Optional[str]:
        """根据 capability 获取对应的 Worker 节点名称"""
        worker = self.CAPABILITY_MAP.get(capability)
        if not worker:
            logger.warning(f"[ToolRegistry] 未知 capability: {capability}")
        return worker

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


# 全局单例
tool_registry = ToolRegistry()
