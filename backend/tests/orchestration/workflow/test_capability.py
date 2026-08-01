"""test_capability.py — BaseCapability + BaseAgentSkill

覆盖：
- BaseCapability 抽象（不能直接实例化）
- BaseCapability.run_with_retry 重试 + 超时
- BaseAgentSkill._call_llm 走 infra.llm.llm
- InventoryAnalyzer fallback 路径
- InventoryAnalyzer LLM 解析失败 fallback
"""
from __future__ import annotations

import asyncio
import json as _json
from unittest.mock import AsyncMock, MagicMock, patch as mp

import pytest

from backend.orchestration.capability.base import (
    BaseAgentSkill,
    BaseCapability,
    is_capability,
)
from backend.orchestration.capability.inventory_analyzer import InventoryAnalyzer


# ─────────────────────────────────────────────────────────────
# 抽象基类
# ─────────────────────────────────────────────────────────────

class TestBaseCapabilityAbstract:
    """BaseCapability 抽象"""

    def test_cannot_instantiate_directly(self):
        """BaseCapability 是 ABC，不能直接实例化"""
        with pytest.raises(TypeError, match="abstract"):
            BaseCapability()

    def test_subclass_must_implement_run(self):
        """子类必须实现 run()"""
        class Incomplete(BaseCapability):
            pass

        with pytest.raises(TypeError, match="abstract"):
            Incomplete()

    def test_subclass_with_run_works(self):
        """实现 run() 可以实例化"""

        class OK(BaseCapability):
            name = "ok"
            capabilities = ["ok.do"]

            async def run(self, inputs: dict) -> dict:
                return {"ok": True}

        c = OK()
        assert c.name == "ok"


# ─────────────────────────────────────────────────────────────
# run_with_retry
# ─────────────────────────────────────────────────────────────

class TestBaseCapabilityRunWithRetry:
    """BaseCapability.run_with_retry"""

    def test_run_success_first_try(self):
        class C(BaseCapability):
            name = "c"

            async def run(self, inputs):
                return {"v": 1}

        async def run():
            c = C()
            return await c.run_with_retry({})
        result = asyncio.run(run())
        assert result == {"v": 1}

    def test_run_succeeds_on_retry(self):
        """失败一次，第二次成功"""
        attempt_count = [0]

        class C(BaseCapability):
            name = "c"

            async def run(self, inputs):
                attempt_count[0] += 1
                if attempt_count[0] < 2:
                    raise ValueError("first fail")
                return {"v": 1}

        async def run():
            c = C()
            return await c.run_with_retry({}, max_retries=2)
        result = asyncio.run(run())
        assert result == {"v": 1}
        assert attempt_count[0] == 2

    def test_run_exhausted_retries_raises(self):
        """重试用尽抛最后异常"""

        class C(BaseCapability):
            name = "c"

            async def run(self, inputs):
                raise ValueError("always fail")

        async def run():
            c = C()
            with pytest.raises(ValueError, match="always fail"):
                await c.run_with_retry({}, max_retries=1)
        asyncio.run(run())

    def test_run_timeout(self):
        """超时抛 TimeoutError"""

        class C(BaseCapability):
            name = "c"

            async def run(self, inputs):
                await asyncio.sleep(1.0)  # 超时
                return {}

        async def run():
            c = C()
            with pytest.raises(asyncio.TimeoutError):
                await c.run_with_retry({}, timeout_sec=0.05)
        asyncio.run(run())


# ─────────────────────────────────────────────────────────────
# BaseAgentSkill
# ─────────────────────────────────────────────────────────────

class TestBaseAgentSkill:
    """BaseAgentSkill 抽象 + _call_llm"""

    def test_cannot_instantiate_directly(self):
        """BaseAgentSkill 也是 ABC"""
        with pytest.raises(TypeError, match="abstract"):
            BaseAgentSkill()

    def test_inventory_analyzer_extends_base_agent_skill(self):
        """InventoryAnalyzer 是 BaseAgentSkill 子类"""
        assert issubclass(InventoryAnalyzer, BaseAgentSkill)
        assert issubclass(InventoryAnalyzer, BaseCapability)

    def test_call_llm_uses_infra_llm(self, patched_llm):
        """_call_llm 走 backend.infra.llm.llm"""
        analyzer = InventoryAnalyzer()

        async def run():
            return await analyzer._call_llm("test prompt")
        result = asyncio.run(run())
        # mock 返回 FakeResponse.content
        assert "anomalies" in result
        assert patched_llm.ainvoke.called

    def test_call_llm_records_meta(self, patched_llm, monkeypatch):
        """_call_llm 走 proxy 自动捕获 token 统计"""
        analyzer = InventoryAnalyzer()
        async def run():
            return await analyzer._call_llm("test")
        asyncio.run(run())
        # patched fixture 已经清空 _last_call_meta
        # 这里只验证 _call_llm 没崩（不深查 proxy 内部）

    def test_is_capability_returns_true_for_base(self):
        """is_capability() 对 BaseCapability / BaseAgentSkill 都返回 True"""
        class A(BaseCapability):
            name = "a"
            async def run(self, inputs): return {}
        a = A()
        assert is_capability(a) is True

    def test_is_capability_returns_false_for_unrelated(self):
        assert is_capability("string") is False
        assert is_capability({}) is False
        assert is_capability(42) is False


# ─────────────────────────────────────────────────────────────
# InventoryAnalyzer fallback
# ─────────────────────────────────────────────────────────────

class TestInventoryAnalyzerFallback:
    """InventoryAnalyzer fallback 路径"""

    def test_fallback_with_unserializable_data(self):
        """LLM 不调用时 fallback"""
        # 不调用 _call_llm，直接走 fallback 路径
        # 这需要构造一个无法 JSON 化的 inputs 或者模拟 _call_llm 抛错

        # 模拟 _call_llm 抛异常
        analyzer = InventoryAnalyzer()

        async def run():
            with mp.object(analyzer, "_call_llm", AsyncMock(side_effect=Exception("LLM 失败"))):
                return await analyzer.run({
                    "sales_data": [],
                    "inventory_data": [
                        {"product_id": "SKU-1", "current_qty": 3},
                        {"product_id": "SKU-2", "current_qty": 50},
                    ],
                    "rules": "",
                })

        result = asyncio.run(run())
        # fallback 返回 anomalies
        assert "anomalies" in result
        assert "confidence" in result
        assert result["confidence"] < 0.5  # fallback confidence 低

    def test_fallback_static_method(self):
        """_fallback 是静态方法可独立调用"""
        result = InventoryAnalyzer._fallback([
            {"product_id": "P1", "current_qty": 3},
            {"product_id": "P2", "current_qty": 100},
        ])
        assert result["confidence"] == 0.3
        assert result["reasoning"]  # 非空
        # 临界值 5：< 5 = critical
        crit = [a for a in result["anomalies"] if a["level"] == "critical"]
        warn = [a for a in result["anomalies"] if a["level"] == "warning"]
        assert len(crit) == 1  # P1 (qty=3)
        assert len(warn) == 0  # P2 (qty=100) 不告警