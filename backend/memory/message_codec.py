"""LangChain 文本消息与 L2 行之间的受控编解码。"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, messages_from_dict, message_to_dict


_ROLE_BY_MESSAGE_TYPE = {"human": "user", "ai": "assistant", "tool": "tool"}


def serialize_message(message: BaseMessage) -> dict[str, Any]:
    """序列化 L1/L2 支持的文本消息，不保存调用方提供的消息 ID。"""
    if not isinstance(message, BaseMessage):
        raise TypeError("L2 只接受 LangChain BaseMessage")

    serialized = message_to_dict(message)
    message_type = serialized.get("type")
    role = _ROLE_BY_MESSAGE_TYPE.get(message_type)
    if role is None:
        raise ValueError(f"L2 不支持 LangChain 消息类型: {message_type!r}")

    data = serialized.get("data")
    if not isinstance(data, dict):
        raise ValueError("LangChain 消息序列化结果缺少 data 对象")
    content = data.get("content")
    if not isinstance(content, str):
        raise ValueError("L2 chat_messages 目前只支持文本 content")

    # content 单独存储，id 使用数据库主键生成；其余类型元数据必须能安全写入 JSONB。
    metadata = {
        key: value for key, value in data.items()
        if key not in {"content", "id", "type"}
    }
    try:
        metadata = json.loads(json.dumps(
            metadata, ensure_ascii=False, allow_nan=False,
        ))
    except (TypeError, ValueError) as exc:
        raise ValueError("LangChain 消息元数据必须可序列化为 JSON") from exc

    return {
        "role": role,
        "content": content,
        "message_type": message_type,
        "message_metadata": metadata,
    }


def restore_message(row: Any) -> BaseMessage:
    """从 L2 行还原消息；旧行沿用 user/assistant 映射并使用稳定 DB ID。"""
    row_id = getattr(row, "id", None)
    message_id = f"chat-message:{row_id}" if row_id is not None else None
    role = getattr(row, "role", None)
    message_type = getattr(row, "message_type", None)
    content = getattr(row, "content", "") or ""

    if message_type is None:
        # 旧数据没有类型列；保持既有行为，user 还原为 Human，其余还原为 AI。
        from langchain_core.messages import AIMessage, HumanMessage
        cls = HumanMessage if role == "user" else AIMessage
        return cls(content=content, id=message_id)

    if _ROLE_BY_MESSAGE_TYPE.get(message_type) != role:
        raise ValueError("L2 消息 role 与 message_type 不匹配")
    metadata = getattr(row, "message_metadata", None)
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("L2 消息 message_metadata 必须为 JSON 对象")

    data = dict(metadata)
    data.update({"content": content, "type": message_type})
    if message_id is not None:
        data["id"] = message_id
    payload = {"type": message_type, "data": data}
    restored = messages_from_dict([payload])[0]
    if not isinstance(restored, BaseMessage):
        raise ValueError("L2 消息未能还原为 LangChain BaseMessage")
    return restored


def role_label(role: str) -> str:
    """返回摘要提示使用的角色标签。"""
    return {"user": "用户", "assistant": "助手", "tool": "工具"}.get(role, "助手")
