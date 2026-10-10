"""从当前有效的结构化记忆构建动态画像投影。"""

from __future__ import annotations


def _profile_value(raw: str):
    """只转换无歧义的布尔字面量，其余值保持记忆契约中的字符串。"""
    normalized = str(raw).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return raw


def build_profile_projection(records) -> dict:
    """按 memory_key 的点分段投影画像，不拼接或总结自由文本。"""
    profile: dict = {}
    for record in records:
        key = getattr(record, "memory_key", None)
        value = getattr(record, "structured_value", None)
        if not key or value is None:
            continue
        segments = [part for part in str(key).split(".") if part]
        if len(segments) < 2:
            continue

        node = profile
        for segment in segments[:-1]:
            child = node.get(segment)
            if child is None:
                child = {}
                node[segment] = child
            if not isinstance(child, dict):
                node = None
                break
            node = child
        if node is None or segments[-1] in node:
            continue
        node[segments[-1]] = _profile_value(value)
    return profile


__all__ = ["build_profile_projection"]
