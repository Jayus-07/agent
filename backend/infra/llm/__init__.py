"""
llm — 多 Provider LLM 工厂 + 运行时切换

目录:
  proxy.py      — _LLMProxy 代理（运行时切换）+ 模块级 llm 单例
  factory.py    — LLMFactory（注册/切换/获取）
  models.py     — AVAILABLE_MODELS + PROVIDERS 注册表
  providers/    — 各 Provider 实现（构建 + 余额查询）

用法:
    from backend.infra.llm import llm                # 代理对象（自动跟随切换）
    from backend.infra.llm import get_llm            # 显式获取当前实例
    from backend.infra.llm import get_llm_factory    # 获取工厂单例（切换模型）
"""

from backend.infra.llm.proxy import llm, get_llm, _LLMProxy
from backend.infra.llm.factory import LLMFactory, get_llm_factory

__all__ = ["llm", "get_llm", "_LLMProxy", "LLMFactory", "get_llm_factory"]
