"""SQL MCP Server — 自然语言查询数据库"""
from mcp_servers.manager import MCPServer
from backend.sql.sql_agent import get_sql_agent


class SQLMCPServer(MCPServer):
    """自然语言 SQL 查询 + 表结构。"""
    name = "sql"
    description = "自然语言查询 PostgreSQL 跨境电商数据库"

    def list_tools(self) -> list:
        return [
            {
                "name": "sql_query",
                "description": "自然语言转 SQL 并执行",
                "parameters": {
                    "question": {"type": "string", "required": True, "description": "查询问题"},
                },
            },
            {
                "name": "list_tables",
                "description": "列出数据库中的所有表",
                "parameters": {},
            },
        ]

    def call_tool(self, tool_name: str, params: dict):
        if tool_name == "sql_query":
            agent = get_sql_agent()
            result = agent.ask(params["question"])
            return {"result": result}

        if tool_name == "list_tables":
            from backend.config import DB_CONFIG
            import psycopg2
            try:
                conn = psycopg2.connect(**DB_CONFIG)
                cur = conn.cursor()
                cur.execute("""
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY table_schema, table_name
                """)
                tables = [f"{row[0]}.{row[1]}" for row in cur.fetchall()]
                cur.close()
                conn.close()
                return {"tables": tables, "count": len(tables)}
            except Exception as e:
                return {"tables": [], "count": 0, "error": str(e)}

        raise ValueError(f"未知 tool: {tool_name}")