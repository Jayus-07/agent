"""
llm_factory — 向后兼容 re-export（新代码请用 llm.proxy / llm.factory）

历史: 旧版单文件 llm/llm_factory.py（354行）已拆分:
  llm/proxy.py    — _LLMProxy + llm 单例 + get_llm()
  llm/factory.py  — LLMFactory（注册/切换/获取）
  llm/models.py   — AVAILABLE_MODELS + PROVIDERS 注册表
  llm/providers/  — ollama.py / deepseek.py

所有 `from llm.llm_factory import llm` 仍可用。
"""

from llm.proxy import llm, get_llm, _LLMProxy
from llm.factory import LLMFactory, get_llm_factory

__all__ = ["llm", "get_llm", "_LLMProxy", "LLMFactory", "get_llm_factory"]
