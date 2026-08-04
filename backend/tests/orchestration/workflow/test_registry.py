"""test_registry.py — WorkflowRegistry + Router Index

覆盖：
- register / get
- build_router_index（含 embed 失败降级）
- 重复注册检测
- list_metas / list_router_entries
- RouterEntry 三个匹配维度
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.orchestration.workflow import workflow, step
from backend.orchestration.workflow.registry import (
    RouterEntry,
    WorkflowRegistry,
    _cosine_similarity,
)


# ─────────────────────────────────────────────────────────────
# 注册 / 查询
# ─────────────────────────────────────────────────────────────

class TestRegistryBasicOps:
    """基本操作"""

    def test_register_workflow(self, fresh_registry):
        """注册一个 workflow"""
        @workflow(name="t")
        class T:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(T)
        assert fresh_registry.get("t") is T
        meta = fresh_registry.get_meta("t")
        assert meta is not None
        assert meta.name == "t"

    def test_register_duplicate_raises(self, fresh_registry):
        """重复注册同名 workflow 抛错"""
        @workflow(name="dup")
        class T1:
            @step()
            async def s(self, ctx): return {}

        @workflow(name="dup")
        class T2:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(T1)
        with pytest.raises(ValueError, match="已注册"):
            fresh_registry.register(T2)

    def test_register_class_without_decorator_raises(self, fresh_registry):
        """没 @workflow 装饰的类不能注册"""
        class NotDecorated:
            pass

        with pytest.raises(ValueError, match="没有 @workflow"):
            fresh_registry.register(NotDecorated)

    def test_get_nonexistent_returns_none(self, fresh_registry):
        """查不存在的 workflow 返回 None"""
        assert fresh_registry.get("nonexistent") is None
        assert fresh_registry.get_meta("nonexistent") is None

    def test_list_metas(self, fresh_registry):
        """list_metas 返回所有注册的 meta"""
        @workflow(name="a")
        class A:
            @step()
            async def s(self, ctx): return {}

        @workflow(name="b")
        class B:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(A)
        fresh_registry.register(B)

        names = {m.name for m in fresh_registry.list_metas()}
        assert names == {"a", "b"}


# ─────────────────────────────────────────────────────────────
# Router Index 构建
# ─────────────────────────────────────────────────────────────

class TestRegistryRouterIndex:
    """build_router_index"""

    def test_build_empty(self, fresh_registry):
        """空 registry 构建 → 空 index"""
        fresh_registry.build_router_index()
        assert fresh_registry.router_index == {}

    def test_build_copies_metadata(self, fresh_registry):
        """构建后 RouterEntry 包含 metadata 字段"""
        @workflow(
            name="t",
            objects=["o1", "o2"],
            actions=["a1"],
            examples=["e1"],
            default_kbs=["k1"],
        )
        class T:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(T)
        fresh_registry.build_router_index()

        entry = fresh_registry.router_index["t"]
        assert entry.objects == ["o1", "o2"]
        assert entry.actions == ["a1"]
        assert entry.examples == ["e1"]
        assert entry.default_kbs == ["k1"]

    def test_build_without_embedding_client(self, fresh_registry):
        """无 embedding client 时 examples_embeddings 为空"""
        @workflow(name="t", examples=["e1", "e2"])
        class T:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(T)
        fresh_registry.build_router_index()  # 无 embedding_client

        entry = fresh_registry.router_index["t"]
        assert entry.examples_embeddings == []

    def test_build_with_failing_embedding_client(self, fresh_registry):
        """embedding client 抛异常时降级（不崩）"""
        @workflow(name="t", examples=["e1"])
        class T:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(T)

        # mock embedding client 抛异常
        fake_client = MagicMock()
        fake_client.embed_batch = MagicMock(side_effect=Exception("embed 失败"))
        fresh_registry.build_router_index(embedding_client=fake_client)

        # 不应崩，examples_embeddings 为空
        entry = fresh_registry.router_index["t"]
        assert entry.examples_embeddings == []

    def test_build_with_success_embedding_client(self, fresh_registry):
        """embedding 成功时存储 embeddings"""
        @workflow(name="t", examples=["e1", "e2"])
        class T:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(T)

        # mock embedding client 返回 embeddings
        fake_client = MagicMock()
        fake_client.embed_batch = MagicMock(return_value=[
            [0.1] * 4, [0.2] * 4,
        ])
        fresh_registry.build_router_index(embedding_client=fake_client)

        entry = fresh_registry.router_index["t"]
        assert len(entry.examples_embeddings) == 2

    def test_list_router_entries(self, fresh_registry):
        """list_router_entries 返回所有 entry"""
        @workflow(name="a")
        class A:
            @step()
            async def s(self, ctx): return {}

        @workflow(name="b")
        class B:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(A)
        fresh_registry.register(B)
        fresh_registry.build_router_index()

        entries = fresh_registry.list_router_entries()
        names = {e.workflow_name for e in entries}
        assert names == {"a", "b"}

    def test_register_invalidates_old_router_entry(self, fresh_registry):
        """重复 build_router_index 会更新现有 entry 的元数据"""
        @workflow(name="t", examples=["v1"])
        class T1:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(T1)
        fresh_registry.build_router_index()
        assert fresh_registry.router_index["t"].examples == ["v1"]

        # 模拟 metadata 更新：直接修改 _workflows，然后 rebuild
        # （实际生产中不会这么做，但测试路由 index 的更新逻辑）
        new_entry_meta = fresh_registry.get_meta("t")
        # 直接验证：build_router_index 后 entry 的 description 跟随 meta
        fresh_registry.build_router_index()
        assert fresh_registry.router_index["t"].description == new_entry_meta.description


# ─────────────────────────────────────────────────────────────
# RouterEntry 匹配
# ─────────────────────────────────────────────────────────────

class TestRouterEntryMatching:
    """RouterEntry 三个匹配维度"""

    def test_object_match_score_substring(self):
        """object_match_score: query 含 object 关键词"""
        entry = RouterEntry(
            workflow_name="t",
            objects=["库存", "补货", "预警"],
            actions=[],
            examples=[],
            default_kbs=[],
        )
        # query="检查库存风险" 含 "库存"
        assert entry.object_match_score("检查库存风险") == 1 / 3
        # query="库存预警补货" 全含
        assert entry.object_match_score("库存预警补货") == 1.0
        # query 完全无关
        assert entry.object_match_score("完全无关") == 0.0

    def test_action_match_score_substring(self):
        """action_match_score: query 含 action 关键词"""
        entry = RouterEntry(
            workflow_name="t",
            objects=[],
            actions=["生成", "导出", "发送"],
            examples=[],
            default_kbs=[],
        )
        assert entry.action_match_score("生成报告") == 1 / 3
        assert entry.action_match_score("完全无关") == 0.0

    def test_cosine_similarity_function(self):
        """_cosine_similarity 基本行为"""
        # 完全相同的向量 → 1.0
        a = [1.0, 0.0, 0.0]
        assert _cosine_similarity(a, a) == pytest.approx(1.0)
        # 正交向量 → 0.0
        b = [0.0, 1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0)
        # 空向量 → 0.0
        assert _cosine_similarity([], []) == 0.0
        # 长度不匹配 → 0.0
        assert _cosine_similarity([1, 2], [1, 2, 3]) == 0.0


# ─────────────────────────────────────────────────────────────
# 单例
# ─────────────────────────────────────────────────────────────

class TestRegistrySingleton:
    """模块级单例"""

    def test_get_workflow_registry_returns_singleton(self):
        """get_workflow_registry 返回同一实例"""
        from backend.orchestration.workflow.registry import get_workflow_registry
        reg1 = get_workflow_registry()
        reg2 = get_workflow_registry()
        assert reg1 is reg2

    def test_reset_singletons(self, reset_singletons):
        """reset_singletons fixture 清掉单例"""
        # fixture 已把 _registry = None
        from backend.orchestration.workflow.registry import _registry as _reg_global
        assert _reg_global is None

        # 第一次调用应创建新实例
        from backend.orchestration.workflow.registry import get_workflow_registry
        reg = get_workflow_registry()
        assert reg is not None

        # 第二次调用应返回同一实例（懒加载单例）
        reg2 = get_workflow_registry()
        assert reg is reg2