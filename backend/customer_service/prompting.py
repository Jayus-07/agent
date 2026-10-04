"""客服域 Prompt 渲染边界。

客服域所有需要 LLM 的节点统一从 PromptService 读取模板，避免在节点内
拼接不可治理的硬编码 Prompt。
"""
from __future__ import annotations

from backend.prompts.service import prompt_service


def render_prompt(key: str, **variables: str) -> str:
    """按 Prompt Key 渲染客服域 Prompt。"""
    return prompt_service.render_sync(key, **variables).text
