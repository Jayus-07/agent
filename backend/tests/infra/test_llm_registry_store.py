"""infra/llm/registry_store.py —— DB 覆盖层刷新与 **fail-open** 契约（P1b 接线）

本文件锁定的是「刷新循环可以失败，但绝不能倒退模型清单」这条语义。
方向必须与守卫开关相反：`sys_config` 是 fail-closed（宁可用默认值），
注册表是 fail-open（保留上次已知值 + 代码层）。

为什么这条值得单独锁：DB 抖动若被当成「覆盖层为空」处理，会让**自建模型
与自建凭据在运行中静默消失**，而症状是「问答突然报未知模型」——真因极难定位。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.infra.llm import models, registry_store
from backend.infra.llm.credentials import ProviderCredentials


@pytest.fixture(autouse=True)
def _clean():
    registry_store.reset_for_tests()
    yield
    registry_store.reset_for_tests()


def _snap(**kw) -> registry_store.RegistrySnapshot:
    return registry_store.RegistrySnapshot(**kw)


# ── 失败方向：保留上次已知值，不清空 ────────────────────────────────────


def test_db_unavailable_keeps_previous_known_layer(monkeypatch):
    """DB 不可用 → 返回 False，且**保留**上次注入的动态层（不回退到代码层）。"""
    models.set_dynamic_models(
        [{"name": "glm-4.6", "provider": "custom", "source": "db"}]
    )
    monkeypatch.setattr(
        registry_store, "load_registry",
        AsyncMock(return_value=_snap(loaded=False)),
    )

    assert asyncio.run(registry_store.refresh_registry()) is False
    # 上一次成功注入的条目必须还在（fail-open 的核心断言）
    assert models.get_model_entry("glm-4.6")["provider"] == "custom"


def test_refresh_loop_survives_exception_and_keeps_looping(monkeypatch):
    """单轮抛异常不得终止循环（首轮失败 = DB 表还没建好，不能就此死掉）。"""
    calls = {"n": 0}

    async def boom() -> bool:
        calls["n"] += 1
        raise RuntimeError("connection refused")

    monkeypatch.setattr(registry_store, "refresh_registry", boom)

    async def drive() -> int:
        task = asyncio.create_task(registry_store.refresh_loop(interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return calls["n"]

    # 至少跑过两轮 → 证明异常后仍在循环
    assert asyncio.run(drive()) >= 2


# ── 成功路径：注入与「空快照 = 零行为变化」 ──────────────────────────────


def test_load_success_injects_model_and_credential(monkeypatch):
    snap = _snap(
        providers=[{"id": "custom", "display_name": "自建"}],
        models=[{"name": "glm-4.6", "provider": "custom", "source": "db"}],
        credentials={
            "custom": ProviderCredentials(
                provider="custom", api_key="sk-x", source="db", version=3
            )
        },
        loaded=True,
    )
    monkeypatch.setattr(registry_store, "load_registry", AsyncMock(return_value=snap))

    assert asyncio.run(registry_store.refresh_registry()) is True
    assert models.get_model_entry("glm-4.6")["provider"] == "custom"
    assert models.resolve_provider("glm-4.6", strict=True) == "custom"


def test_loaded_but_empty_snapshot_clears_dynamic_layer(monkeypatch):
    """表存在但为空（首次部署）→ 动态层清空 → 清单为空。

    §B.15 起代码层种子已退役：DB 空表意味着清单**确实为空**（不再是
    「回落代码层」）。启动校验会告警缺模型，这是期望行为而非回归。
    """
    models.set_dynamic_models([{"name": "glm-4.6", "provider": "custom"}])
    monkeypatch.setattr(
        registry_store, "load_registry",
        AsyncMock(return_value=_snap(loaded=True)),   # 三表全空
    )

    assert asyncio.run(registry_store.refresh_registry()) is True
    assert models.get_available_models() == []


def test_reset_for_tests_clears_both_layers():
    models.set_dynamic_models([{"name": "glm-4.6", "provider": "custom"}])
    registry_store.reset_for_tests()
    assert models.get_available_models() == []
