"""admin_llm.py — 管理端治理 LLM 辅助（admin_tools / admin_mcp 共用）

AI 失败分析（决策 #20）与 MCP 语义契约（决策 #19）是同形态的治理动作：
prompt 渲染 → LLM 生成 JSON → 解析重试。本模块收敛两者原先各自复制的
三件套，避免两处实现漂移：

- ``redact_urls``      prompt 输入 URL 清洗（ADR-0011 D7：任何出口不含凭据，
                       包括 URL 内嵌形态）；
- ``invoke_admin_llm`` 统一 LLM 调用（走 backend.infra.llm 单例，同步 invoke
                       经 to_thread，兼容 LangChain 多块 content）；
- ``current_llm_model`` 当前模型名留痕（软失败留空，不影响治理结果）。
"""
from __future__ import annotations

import asyncio
import re

_URL_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://\S+")


def redact_urls(text: str) -> str:
    """prompt 输入清洗：任何形如 scheme://... 的字符串替换为占位符，
    防止上游描述/Schema 中携带的 URL（可能内嵌凭据）进入 LLM 出口。"""
    return _URL_PATTERN.sub("[URL已屏蔽]", str(text or ""))


async def invoke_admin_llm(prompt_text: str) -> str:
    """调用当前 LLM 生成 JSON（同步 invoke 经 to_thread 执行）。"""
    from backend.infra.llm import llm

    resp = await asyncio.to_thread(
        llm.invoke,
        [("system", prompt_text), ("human", "请只输出 JSON 结果。")],
    )
    content = getattr(resp, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 多块内容（LangChain content blocks）
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content)
    return str(content)


def current_llm_model() -> str:
    """当前模型名（软失败留空：只作留痕展示，不影响治理结果）。"""
    try:
        from backend.infra.llm import get_llm_factory

        return str(get_llm_factory().get_current_model_name() or "")
    except Exception:  # noqa: BLE001 — 模型名读取失败不影响落库
        return ""
