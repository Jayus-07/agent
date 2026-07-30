"""workflow/registry.py — WorkflowRegistry + Router Index

设计原则：
- 注册中心：workflow 类通过 register() 注入
- Router Index：启动时扫描所有 workflow metadata 生成
  - 含 examples embedding（启动时一次性算）
  - 含 objects / actions / default_kbs 反向索引
- 不硬编码业务对象词典：新增 workflow 自动加入 Router Index
"""
from __future__ import annotations

import threading
from typing import Any, TYPE_CHECKING
from dataclasses import dataclass, field

from backend.orchestration.workflow.meta import (
    WorkflowMeta,
    StepConfig,
    get_workflow_meta,
    collect_step_methods,
)
from backend.shared.logger import logger

if TYPE_CHECKING:
    from backend.infra.llm.embeddings import EmbeddingClient  # 仅类型检查


@dataclass
class RouterEntry:
    """单个 workflow 在 Router Index 中的条目"""
    workflow_name: str
    objects: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    examples_embeddings: list[list[float]] = field(default_factory=list)  # 与 examples 一一对应
    default_kbs: list[str] = field(default_factory=list)
    description: str = ""

    def object_match_score(self, query: str) -> float:
        """业务对象维度匹配（0~1）

        简单关键词包含匹配：query 含 objects 任一元素 → 命中
        """
        if not self.objects:
            return 0.0
        hits = sum(1 for obj in self.objects if obj in query)
        return hits / len(self.objects)

    def action_match_score(self, query: str) -> float:
        """动作维度匹配（0~1）"""
        if not self.actions:
            return 0.0
        hits = sum(1 for act in self.actions if act in query)
        return hits / len(self.actions)

    def examples_embedding_match_score(self, query_embedding: list[float]) -> float:
        """examples 相似度匹配（0~1）

        返回 query 与任一 example 的最大余弦相似度
        """
        if not self.examples_embeddings:
            return 0.0
        max_sim = 0.0
        for ex_emb in self.examples_embeddings:
            sim = _cosine_similarity(query_embedding, ex_emb)
            max_sim = max(max_sim, sim)
        return max_sim


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class WorkflowRegistry:
    """Workflow 注册中心

    用法：
        registry = WorkflowRegistry()
        registry.register(DailyReport)  # 类，不是实例
        registry.build_router_index()   # 启动时调一次
        router_index = registry.router_index  # Task Router 使用
    """

    def __init__(self, embedding_client: Any | None = None):
        self._workflows: dict[str, type] = {}  # name → class
        self._metas: dict[str, WorkflowMeta] = {}  # name → WorkflowMeta
        self.router_index: dict[str, RouterEntry] = {}  # name → RouterEntry
        self._embedding_client = embedding_client
        self._lock = threading.RLock()
        logger.info("[WorkflowRegistry] 初始化")

    def register(self, cls: type) -> None:
        """注册一个 Workflow 类（读 @workflow 写入的 WorkflowMeta）

        Args:
            cls: 被 @workflow 装饰过的类
        """
        meta = get_workflow_meta(cls)
        if meta is None:
            raise ValueError(
                f"{cls.__name__} 没有 @workflow 装饰器，无法注册"
            )
        with self._lock:
            if meta.name in self._workflows:
                raise ValueError(f"Workflow {meta.name!r} 已注册")
            self._workflows[meta.name] = cls
            self._metas[meta.name] = meta
            # 清空旧 Router Index（让 build_router_index 重新生成）
            self.router_index.pop(meta.name, None)
        logger.info(f"[WorkflowRegistry] 注册 Workflow: {meta.name}")

    def get(self, name: str) -> type | None:
        """获取 workflow class"""
        return self._workflows.get(name)

    def get_meta(self, name: str) -> WorkflowMeta | None:
        """获取 WorkflowMeta"""
        return self._metas.get(name)

    def list_metas(self) -> list[WorkflowMeta]:
        """列出所有 WorkflowMeta"""
        return list(self._metas.values())

    def list_router_entries(self) -> list[RouterEntry]:
        """列出所有 RouterEntry（Task Router 使用）"""
        return list(self.router_index.values())

    def collect_steps(self, name: str) -> dict[str, tuple[Any, StepConfig]]:
        """获取 workflow class 上所有 step 方法"""
        cls = self._workflows.get(name)
        if cls is None:
            return {}
        return collect_step_methods(cls)

    def build_router_index(self, embedding_client: Any | None = None) -> None:
        """扫描所有 workflow metadata，构建 Router Index

        包含：
        - objects / actions: 直接从 metadata 拷贝
        - examples: 直接从 metadata 拷贝
        - examples_embeddings: 启动时一次性 embed（如果提供 embedding_client）
        - default_kbs: 直接从 metadata 拷贝
        """
        client = embedding_client or self._embedding_client
        with self._lock:
            self.router_index.clear()
            for name, meta in self._metas.items():
                entry = RouterEntry(
                    workflow_name=name,
                    objects=list(meta.objects),
                    actions=list(meta.actions),
                    examples=list(meta.examples),
                    default_kbs=list(meta.default_kbs),
                    description=meta.description,
                )
                # 启动时算 examples embedding（一次性）
                if client is not None and meta.examples:
                    try:
                        entry.examples_embeddings = client.embed_batch(meta.examples)
                    except Exception as e:
                        logger.warning(
                            f"[WorkflowRegistry] embed {name} examples 失败: {e}"
                        )
                self.router_index[name] = entry
                logger.debug(
                    f"[WorkflowRegistry] Router Index: {name} "
                    f"(objects={len(entry.objects)}, "
                    f"actions={len(entry.actions)}, "
                    f"examples={len(entry.examples)})"
                )
        logger.info(
            f"[WorkflowRegistry] Router Index 构建完成: "
            f"{len(self.router_index)} 个 workflow"
        )


# 模块级单例（按需懒加载）
_registry: WorkflowRegistry | None = None


def get_workflow_registry() -> WorkflowRegistry:
    global _registry
    if _registry is None:
        _registry = WorkflowRegistry()
    return _registry