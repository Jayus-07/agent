"""测试与向下穿透兼容辅助函数。

确保存量测试针对 backend.rag.chain 模块级变量（如 llm, get_context,
ENABLE_HISTORY_AWARE_RETRIEVAL, ENABLE_TOKEN_STREAMING 等）的 monkeypatch
能够无缝穿透到专职子模块内部。
"""
from __future__ import annotations

import sys

from backend.config import (
    ENABLE_HISTORY_AWARE_RETRIEVAL as _DEFAULT_ENABLE_HISTORY_AWARE_RETRIEVAL,
)
from backend.config import (
    ENABLE_TOKEN_STREAMING as _DEFAULT_ENABLE_TOKEN_STREAMING,
)
from backend.config import (
    EVIDENCE_TOKEN_BUDGET as _DEFAULT_EVIDENCE_TOKEN_BUDGET,
)
from backend.infra.llm import llm as _default_llm
from backend.rag.context import get_context as _default_get_context


def get_chain_context():
    """获取当前 RAG 上下文，优先读取被 monkeypatch 的 backend.rag.chain.get_context。"""
    chain_mod = sys.modules.get("backend.rag.chain")
    if chain_mod is not None and hasattr(chain_mod, "get_context"):
        try:
            return chain_mod.get_context()
        except Exception:
            pass
    return _default_get_context()


def get_chain_llm():
    """获取 LLM 实例，优先读取被 monkeypatch 的 backend.rag.chain.llm。"""
    chain_mod = sys.modules.get("backend.rag.chain")
    if chain_mod is not None and hasattr(chain_mod, "llm"):
        return chain_mod.llm
    return _default_llm


def get_enable_history_aware() -> bool:
    """获取是否启用历史感知检索配置，优先读取 backend.rag.chain。"""
    chain_mod = sys.modules.get("backend.rag.chain")
    if chain_mod is not None and hasattr(chain_mod, "ENABLE_HISTORY_AWARE_RETRIEVAL"):
        return bool(chain_mod.ENABLE_HISTORY_AWARE_RETRIEVAL)
    return bool(_DEFAULT_ENABLE_HISTORY_AWARE_RETRIEVAL)


def get_enable_token_streaming() -> bool:
    """获取是否启用 token 流式传输配置，优先读取 backend.rag.chain。"""
    chain_mod = sys.modules.get("backend.rag.chain")
    if chain_mod is not None and hasattr(chain_mod, "ENABLE_TOKEN_STREAMING"):
        return bool(chain_mod.ENABLE_TOKEN_STREAMING)
    return bool(_DEFAULT_ENABLE_TOKEN_STREAMING)


def get_evidence_token_budget() -> int:
    """获取证据 token 预算，优先读取 backend.rag.chain。"""
    chain_mod = sys.modules.get("backend.rag.chain")
    if chain_mod is not None and hasattr(chain_mod, "EVIDENCE_TOKEN_BUDGET"):
        try:
            return int(chain_mod.EVIDENCE_TOKEN_BUDGET)
        except (TypeError, ValueError):
            pass
    return int(_DEFAULT_EVIDENCE_TOKEN_BUDGET)
