"""mcp_servers/schema_adapter.py — LangChain Tool → MCP 工具元数据适配

消除手写参数漂移：MCP server 的 list_tools() 不再手写参数表，
从 LangChain tool 的 pydantic args_schema（单一事实来源）自动派生。
tool 层改参数（如 kb_id 增加枚举）→ MCP tools/list 自动同步。
"""
from typing import Any


def _json_type(pyl_type: str) -> str:
    """JSON Schema 类型映射（pydantic v2 → JSON Schema 基本类型）。"""
    return {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "array": "array",
        "object": "object",
    }.get(pyl_type, "string")


def langchain_tool_to_mcp_meta(
    tool,
    name: str = "",
    description: str = "",
) -> dict[str, Any]:
    """从 LangChain tool 派生 MCP list_tools 条目。

    Args:
        tool: LangChain StructuredTool（含 args_schema / description / name）
        name: 覆写 MCP 工具名（tool.name 常带 _tool 后缀，MCP 侧用短名）
        description: 覆写描述（MCP 面向外部 client，可与面向 Planner 的描述不同）
    """
    parameters: dict[str, Any] = {}
    if tool.args_schema is not None:
        schema = tool.args_schema.model_json_schema()
        required = set(schema.get("required", []))
        for pname, prop in schema.get("properties", {}).items():
            entry: dict[str, Any] = {
                "type": _json_type(prop.get("type", "string")),
                "required": pname in required,
                "description": prop.get("description", ""),
            }
            if "default" in prop:
                entry["default"] = prop["default"]
            parameters[pname] = entry
    return {
        "name": name or tool.name,
        "description": (description or (tool.description or "")).strip(),
        "parameters": parameters,
    }
