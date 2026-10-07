"""客服域 Prompt 渲染边界。

客服域所有需要 LLM 的节点统一从 PromptService 读取模板，避免在节点内
拼接不可治理的硬编码 Prompt。
"""
from __future__ import annotations

from backend.prompts.service import prompt_service


def render_prompt(key: str, **variables: str) -> str:
    """按 Prompt Key 渲染客服域 Prompt。"""
    return prompt_service.render_sync(key, **variables).text


def render_prompt_with_version(key: str, **variables: str) -> tuple[str, int | None]:
    """渲染并返回生效版本（trace cs_*_prompt_version 打点用）。

    版本随 render_sync 内部的 record_prompt_version 统一落旁路；
    此处返回值供调用方写自有 tag（语义层/回复层归因）。渲染失败由
    调用方处理（返回前异常向上抛，与 render_prompt 一致）。
    """
    result = prompt_service.render_sync(key, **variables)
    return result.text, result.version
