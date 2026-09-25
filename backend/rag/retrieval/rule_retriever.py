"""规则召回兼容入口。

RuleBasedRetriever 已归档到 :mod:`multi_path_retrieval`；保留此模块名，
让外部集成与旧版评测脚本可以平滑迁移，不复制第二份实现。
"""

from backend.rag.retrieval.multi_path_retrieval import RuleBasedRetriever

__all__ = ["RuleBasedRetriever"]
