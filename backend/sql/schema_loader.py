"""
schema_loader.py — 加载表结构、白名单、敏感列配置

设计意图：
  - 所有安全策略集中在 schema_config.py 中
  - 此模块提供统一的只读访问接口
  - 后续可扩展为从数据库 INFORMATION_SCHEMA 动态加载
"""
from typing import Dict, List, Set, Any

from backend.sql.data.schema_config import SCHEMA_CONFIG


class SchemaLoader:
    """加载并缓存 schema 配置，提供快速查询接口"""

    def __init__(self):
        self._config = SCHEMA_CONFIG

        # — 预计算快查结构 —
        self.allowed_tables: Set[str] = set(self._config["tables"].keys())

        self.sensitive_columns: Set[str] = set(self._config["sensitive_columns"])

        self.masked_columns: Dict[str, tuple] = self._config["masked_columns"]

        self.row_security: Dict[str, dict] = self._config["row_security"]

        self.banned_functions: Set[str] = {
            f.upper() for f in self._config["banned_functions"]
        }

        self.max_limit: int = self._config["max_limit"]
        self.query_timeout: float = self._config["query_timeout"]

    # =================================================
    # 表结构查询
    # =================================================

    def get_all_table_names(self) -> List[str]:
        return list(self.allowed_tables)

    def get_table_description(self, table_name: str) -> str:
        t = self._config["tables"].get(table_name)
        return t.get("description", "") if t else ""

    def get_table_info(self, table_names: List[str] = None) -> str:
        """生成给 LLM 用的表结构描述文本"""
        if table_names is None:
            table_names = list(self.allowed_tables)

        parts = []
        for tname in table_names:
            if tname not in self.allowed_tables:
                continue
            t = self._config["tables"][tname]
            cols = t["columns"]
            # 排除敏感列（不给 LLM 看到）
            visible_cols = {
                c: desc for c, desc in cols.items()
                if f"{tname}.{c}" not in self.sensitive_columns
            }
            col_lines = "\n".join(f"    {c}: {desc}" for c, desc in visible_cols.items())
            parts.append(
                f"表名: {tname}\n"
                f"描述: {t.get('description', '')}\n"
                f"列:\n{col_lines}"
            )
        return "\n\n".join(parts)

    # =================================================
    # 安全检查（行级安全）
    # =================================================

    def get_row_security(self, table_name: str) -> dict:
        return self.row_security.get(table_name, {})

    # =================================================
    # 动态表注册（demo/测试用）
    # =================================================

    def register_table(self, table_name: str, columns: Dict[str, str],
                       description: str = "") -> None:
        """注册临时表，demo 或测试中动态创建的表可通过此方法注入"""
        tname = table_name.lower()
        self.allowed_tables.add(tname)
        self._config["tables"][tname] = {
            "columns": columns,
            "description": description or f"动态注册表: {table_name}",
        }


# 全局单例
schema_loader = SchemaLoader()
