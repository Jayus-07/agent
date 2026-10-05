"""
schema_loader.py — 多业务域 schema 加载器

支持：
  - 多 schema（product / order / inventory / customer / crawler / finance / ai）
  - 表名以 `schema.table` 全限定形式加载
  - 反向索引：schema → [tables]（供 router / validator 使用）
  - 表数据域策略（table_policies → TablePolicy，供 SQLPolicyGuard 消费）

所有安全策略集中在 schema_config.py，本模块只做加载与索引构建。
"""
from dataclasses import dataclass
from typing import Dict, List, Set, Any

from backend.config import SQL_ROW_SECURITY_ENABLED
from backend.sql.data.schema_config import SCHEMA_CONFIG


@dataclass(frozen=True)
class TablePolicy:
    """单表数据域策略（schema_config.table_policies 的类型化形态）。

    语义见 schema_config.py「表数据域策略」段注释（STOP B 决策 D1-D4）：
    data_domain ∈ shared | personal | internal；三个 *_column 是 scope
    注入能力位（声明才注入，不虚构不存在的列）。
    """

    data_domain: str = "shared"
    tenant_column: str | None = None
    department_column: str | None = None
    self_column: str | None = None


class SchemaLoader:
    """加载并缓存 schema 配置，提供快速查询接口"""

    def __init__(self):
        self._config = SCHEMA_CONFIG

        # — Schema 域白名单（防止 LLM 引用不存在的 schema）—
        self.allowed_schemas: Set[str] = set(self._config.get("schemas", set()))

        # — 表结构：以 schema-qualified 名（如 `product.products`）为键 —
        self.allowed_tables: Set[str] = set(self._config["tables"].keys())

        # — 反向索引：schema → 表名集 —
        self._tables_by_schema: Dict[str, Set[str]] = {}
        for qualified_name in self.allowed_tables:
            schema_name = qualified_name.split(".", 1)[0]
            self._tables_by_schema.setdefault(schema_name, set()).add(qualified_name)

        # — 表数据域策略（未登记表默认 shared / 无归属列，向后兼容）—
        self.table_policies: Dict[str, TablePolicy] = {
            qname: TablePolicy(**cfg)
            for qname, cfg in self._config.get("table_policies", {}).items()
        }

        # — 敏感列 / 脱敏列 / 行级安全 / 黑名单 / 限制 —
        self.sensitive_columns: Set[str] = set(self._config["sensitive_columns"])
        self.masked_columns: Dict[str, tuple] = self._config["masked_columns"]
        # 行级安全受 SQL_ROW_SECURITY_ENABLED 总开关控制（P1-11）：
        # 默认关闭（Demo 查询无用户上下文，严格模式会全部拒绝）；
        # 生产开启后 schema_config.row_security 配置的表强制按用户隔离。
        if SQL_ROW_SECURITY_ENABLED:
            self.row_security: Dict[str, dict] = dict(self._config["row_security"])
        else:
            self.row_security: Dict[str, dict] = {}
        self.banned_functions: Set[str] = {
            f.upper() for f in self._config["banned_functions"]
        }
        # G-17 fail-closed 白名单：pg_* 系统函数族默认全拒，只放行此处显式
        # 批准的（默认空）——新型系统函数不再依赖事后补黑名单。
        self.allowed_functions: Set[str] = {
            f.upper() for f in self._config.get("allowed_functions", ())
        }
        self.max_limit: int = self._config["max_limit"]
        self.query_timeout: float = self._config["query_timeout"]

    # =================================================
    # 表结构查询
    # =================================================

    def get_all_table_names(self) -> List[str]:
        return sorted(self.allowed_tables)

    def get_table_description(self, qualified_name: str) -> str:
        t = self._config["tables"].get(qualified_name)
        return t.get("description", "") if t else ""

    def get_browse_columns(self, qualified_name: str) -> Dict[str, str]:
        """表浏览/程序化消费用可见列（声明序 dict：列名 → 注释）。

        与 get_table_info 的 LLM 视图同一敏感列剔除语义（G2：剔除逻辑
        只此一份）；未登记表返回空 dict，调用方 fail-fast。
        """
        t = self._config["tables"].get(qualified_name)
        if not t:
            return {}
        table_sensitive = {
            s.split(".")[-1] for s in self.sensitive_columns
            if s.startswith(qualified_name + ".")
        }
        return {
            c: desc for c, desc in t["columns"].items()
            if qualified_name + "." + c not in self.sensitive_columns
            and c not in table_sensitive
        }

    def get_table_info(self, qualified_names: List[str] = None) -> str:
        """生成给 LLM 用的表结构描述文本。

        入参接受 schema-qualified 表名；空表列表默认所有表。
        输出会在头部标 schema 域，便于 LLM 决定跨域 join。
        兼容：传入裸表名（如 'products'）时，自动 reverse 解析为 'product.products'。
        """
        if qualified_names is None:
            qualified_names = list(self.allowed_tables)

        # reverse-resolve：把裸名映射回 schema-qualified 名
        bare_to_qualified: Dict[str, str] = {}
        for qname in self.allowed_tables:
            bare_to_qualified[qname.split(".", 1)[-1]] = qname

        parts = []
        for qname in qualified_names:
            # 解析为 schema-qualified 名
            if qname in self.allowed_tables:
                resolved = qname
            elif qname in bare_to_qualified:
                resolved = bare_to_qualified[qname]
            else:
                continue
            cols = self.get_browse_columns(resolved)
            col_lines = "\n".join(f"    {c}: {desc}" for c, desc in cols.items())
            t = self._config["tables"][resolved]
            parts.append(
                f"表名: {resolved}\n"
                f"描述: {t.get('description', '')}\n"
                f"列:\n{col_lines}"
            )
        return "\n\n".join(parts)

    def get_tables_in_schema(self, schema_name: str) -> List[str]:
        return sorted(self._tables_by_schema.get(schema_name, set()))

    def split_qualified(self, qualified_name: str) -> tuple:
        """'product.products' → ('product', 'products')；仅有 table → ('', 'products')"""
        if "." in qualified_name:
            schema, table = qualified_name.split(".", 1)
            return schema.lower(), table.lower()
        return "", qualified_name.lower()

    # =================================================
    # 行级安全
    # =================================================

    def get_row_security(self, qualified_name: str) -> dict:
        """row_security 的键是 schema-qualified 表名。"""
        return self.row_security.get(qualified_name, {})

    # =================================================
    # 表数据域策略（SQLPolicyGuard 消费）
    # =================================================

    def get_table_policy(self, qualified_name: str) -> TablePolicy:
        """按 schema-qualified 或裸表名取表策略。

        匹配顺序与 row_security 一致：qualified 命中 → 裸名精确 →
        裸名反向匹配限定键（orders → order.orders）。
        未登记表返回默认策略（shared / 无归属列）——白名单外的表在
        validator Layer 2 已被拒绝，这里只服务已登记表。
        """
        qname = qualified_name.lower()
        if qname in self.table_policies:
            return self.table_policies[qname]
        bare = self.split_qualified(qname)[1]
        for key, policy in self.table_policies.items():
            if key == bare or key.endswith(f".{bare}"):
                return policy
        return TablePolicy()

    # =================================================
    # 动态表注册（demo/测试用，未来可对接 INFORMATION_SCHEMA）
    # =================================================

    def register_table(self, qualified_name: str, columns: Dict[str, str],
                       description: str = "",
                       policy: TablePolicy | None = None) -> None:
        """注册临时表（demo 用）。qualified_name 形如 `tenant.tmp_orders`。

        policy：同步登记数据域策略（fixture 测试 tenant/department 列用）；
        缺省默认策略（shared / 无归属列）。
        """
        qname = qualified_name.lower()
        schema_name = qname.split(".", 1)[0] if "." in qname else "public"
        if schema_name not in self.allowed_schemas and schema_name != "public":
            self.allowed_schemas.add(schema_name)
        self.allowed_tables.add(qname)
        self._tables_by_schema.setdefault(schema_name, set()).add(qname)
        self._config["tables"][qname] = {
            "columns": columns,
            "description": description or f"动态注册表: {qualified_name}",
        }
        if policy is not None:
            self.table_policies[qname] = policy


# 全局单例
schema_loader = SchemaLoader()
