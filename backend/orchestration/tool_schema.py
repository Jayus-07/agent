"""tool_schema.py — params_schema → OpenAI function calling schema 转换器

单一事实来源仍是 Skill 自身的 params_schema（ADR-0001），本模块只做视图
转换，供 tool_selector 节点 bind_tools 使用。此前 LLM 决策点（Planner/
LLMRouter）消费的是 format_params_schema() 渲染的文本；function calling
需要结构化的 OpenAI tools 元素，两套视图同源于 Skill 声明，不会漂移。

转换规则：
  - OpenAI function name 不允许 `.`：capability 的 `.` 转为 `__`
    （sql.query → sql__query），反向映射见 function_name_to_capability
  - "int" 归一为 JSON Schema 的 "integer"（base 校验两种写法都接受）
  - auto=True 的参数（如 business.analyze 的 sql_result）由运行时
    previous_outputs 自动注入，不暴露给模型填写
  - 旧式字符串声明视为 string 可选参数
"""

from __future__ import annotations

import json
import re
from typing import Optional

from backend.orchestration.capability_registry import tool_registry

# OpenAI function name 约束: ^[a-zA-Z0-9_-]+$
_FUNC_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# params_schema type → JSON Schema type
_JSON_TYPES = {
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}


def capability_to_function_name(capability: str) -> str:
    """capability → 合法 OpenAI function name（`.` → `__`）。"""
    name = capability.replace(".", "__")
    if not _FUNC_NAME_RE.match(name):
        raise ValueError(
            f"capability {capability!r} 无法转为合法 function name: {name!r}"
        )
    return name


def function_name_to_capability(name: str) -> str:
    """function name → capability（反向映射；capability 约定不含 `__`）。"""
    return name.replace("__", ".")


def capability_to_function(capability: str) -> Optional[dict]:
    """capability → OpenAI tools 列表元素；未注册的 capability 返回 None。"""
    schema = tool_registry.get_schema(capability)
    if not schema:
        return None

    properties: dict = {}
    required: list[str] = []
    for pname, spec in schema["params"].items():
        if isinstance(spec, str):
            # 旧式声明：视为 string 可选
            properties[pname] = {"type": "string", "description": spec}
            continue
        if spec.get("auto"):
            # 运行时自动注入（previous_outputs），不由模型填写
            continue
        prop = {
            "type": _JSON_TYPES.get(spec.get("type", "string"), "string"),
            "description": spec.get("description", ""),
        }
        if spec.get("enum"):
            prop["enum"] = list(spec["enum"])
        properties[pname] = prop
        if spec.get("required"):
            required.append(pname)

    desc = schema["description"]
    example = schema.get("示例")
    if example:
        desc += f"\n参数示例: {json.dumps(example, ensure_ascii=False)}"

    return {
        "type": "function",
        "function": {
            "name": capability_to_function_name(capability),
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def capabilities_to_tools(capabilities: list[str]) -> tuple[list[dict], dict[str, str]]:
    """批量转换候选 capability。

    Returns:
        (tools, fn2cap) — tools 传给 bind_tools；fn2cap 把模型返回的
        function name 映射回 capability（未注册的 capability 静默跳过）
    """
    tools: list[dict] = []
    fn2cap: dict[str, str] = {}
    for cap in capabilities:
        fn = capability_to_function(cap)
        if fn is None:
            continue
        tools.append(fn)
        fn2cap[fn["function"]["name"]] = cap
    return tools, fn2cap
