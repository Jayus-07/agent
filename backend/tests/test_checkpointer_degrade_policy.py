"""tests/test_checkpointer_degrade_policy.py — 检查点降级判据（结构病审查 P2-10）

病史：主图（`orchestration/graph/checkpointer.py`）与客服域（`customer_service/
graph_builder.py`）在 PostgresSaver 初始化失败时，历史上是「一条 logger.warning +
静默降级 MemorySaver」，MemorySaver 再失败就返回 None（等于没有 checkpointer）。
本地开发无害；生产环境则等于悄悄失去跨轮上下文（interrupt/resume 失效、多副本各
存一份），而日志里的 "enabled" 会让人以为已持久化。

本文件钉三件事：
  1. **判据本身**：开发降级 / 生产硬失败 / 显式放行后降级（期望值写字面量）
  2. **两个消费方真的走判据**：不是各自再写一份 warning 降级
  3. **边界**：旅游域**有意不接入** —— 它把降级作为 PERSISTENCE_DEGRADED 上浮到
     trace 与行程单，属「已披露的降级」，不是静默吞掉，故只降级不硬失败

启动期那道拦截（开关开着但缺 psycopg → production fatal）在
`tests/test_startup_validation.py::TestCheckpointerDriverProbe` 里，复用其环境隔离夹具。
"""
from __future__ import annotations

import sys
import types

import pytest

from backend.config import checkpointer as cp_policy
from backend.config.checkpointer import (
    CheckpointerUnavailable,
    degrade_allowed,
    degrade_or_raise,
)


@pytest.fixture
def dev(monkeypatch):
    monkeypatch.setattr(cp_policy, "ENVIRONMENT", "development")
    monkeypatch.delenv("CHECKPOINTER_ALLOW_DEGRADE", raising=False)


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setattr(cp_policy, "ENVIRONMENT", "production")
    monkeypatch.delenv("CHECKPOINTER_ALLOW_DEGRADE", raising=False)


@pytest.fixture
def no_postgres_driver(monkeypatch):
    """让 `import psycopg` 直接抛 ImportError（sys.modules 置 None 是标准手法）。"""
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", None)


@pytest.fixture
def broken_postgres(monkeypatch):
    """psycopg 在、但连不上（PG 宕机 / 口令错）：连到一半失败，走同一条 except。"""
    fake = types.ModuleType("psycopg")

    def _boom(*args, **kwargs):
        raise OSError("connection refused")

    fake.Connection = types.SimpleNamespace(connect=_boom)
    monkeypatch.setitem(sys.modules, "psycopg", fake)


# ============================================================
# 一、判据本身
# ============================================================
class TestPolicyDecision:
    def test_development_degrades_and_returns_message(self, dev):
        msg = degrade_or_raise("XGraph", "后端不可用")
        assert "[XGraph]" in msg
        assert "MemorySaver" in msg
        # 必须说清后果，否则用户仍以为在持久化
        assert "重启即失" in msg

    def test_production_raises_instead_of_degrading(self, prod):
        with pytest.raises(CheckpointerUnavailable) as exc:
            degrade_or_raise("XGraph", "后端不可用")
        text = str(exc.value)
        assert "[XGraph]" in text
        assert "后端不可用" in text
        # 报错必须给出可执行的处置路径，而不是只说「失败了」
        assert "CHECKPOINTER_ALLOW_DEGRADE=true" in text
        assert "*_CHECKPOINTER_ENABLED" in text

    def test_production_with_explicit_optin_degrades(self, prod, monkeypatch):
        monkeypatch.setenv("CHECKPOINTER_ALLOW_DEGRADE", "true")
        msg = degrade_or_raise("XGraph", "后端不可用")
        assert "MemorySaver" in msg

    def test_optin_is_live_read_not_import_snapshot(self, dev, monkeypatch):
        """开关必须现读 env：运维改 env 后不该还要重启进程才生效。"""
        assert degrade_allowed() is False
        monkeypatch.setenv("CHECKPOINTER_ALLOW_DEGRADE", "TRUE")
        assert degrade_allowed() is True
        monkeypatch.setenv("CHECKPOINTER_ALLOW_DEGRADE", "no")
        assert degrade_allowed() is False

    @pytest.mark.parametrize(
        "raw,expected",
        [("1", True), ("true", True), ("yes", True), ("0", False),
         ("false", False), ("", False), ("off", False)],
    )
    def test_flag_literal_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("CHECKPOINTER_ALLOW_DEGRADE", raw)
        assert degrade_allowed() is expected


# ============================================================
# 二、两个消费方
# ============================================================
class TestMainGraphConsumer:
    def test_degrades_in_development(self, dev, no_postgres_driver, monkeypatch):
        import backend.orchestration.graph.checkpointer as gcp

        monkeypatch.setattr(gcp, "MAIN_GRAPH_CHECKPOINTER_ENABLED", True)
        cp = gcp.build_main_checkpointer()
        assert cp is not None, "开发环境应降级续跑，而不是拿不到 checkpointer"

    def test_raises_in_production(self, prod, broken_postgres, monkeypatch):
        import backend.orchestration.graph.checkpointer as gcp

        monkeypatch.setattr(gcp, "MAIN_GRAPH_CHECKPOINTER_ENABLED", True)
        with pytest.raises(CheckpointerUnavailable) as exc:
            gcp.build_main_checkpointer()
        assert "MainGraph" in str(exc.value)

    def test_disabled_switch_still_returns_none(self, prod, monkeypatch):
        """没开启开关就与降级判据无关 —— 明确「不使用」，不是「假装在用」。"""
        import backend.orchestration.graph.checkpointer as gcp

        monkeypatch.setattr(gcp, "MAIN_GRAPH_CHECKPOINTER_ENABLED", False)
        assert gcp.build_main_checkpointer() is None


class TestCsGraphConsumer:
    def test_degrades_in_development(self, dev, broken_postgres, monkeypatch):
        from backend.customer_service.graph_builder import _build_checkpointer

        monkeypatch.setattr(
            "backend.config.customer_service.CS_CHECKPOINTER_ENABLED", True)
        monkeypatch.setattr(
            "backend.config.customer_service.CS_CHECKPOINTER_BACKEND", "postgres")
        assert _build_checkpointer() is not None

    def test_raises_in_production(self, prod, broken_postgres, monkeypatch):
        from backend.customer_service.graph_builder import _build_checkpointer

        monkeypatch.setattr(
            "backend.config.customer_service.CS_CHECKPOINTER_ENABLED", True)
        monkeypatch.setattr(
            "backend.config.customer_service.CS_CHECKPOINTER_BACKEND", "postgres")
        with pytest.raises(CheckpointerUnavailable) as exc:
            _build_checkpointer()
        assert "CS Graph" in str(exc.value)

    def test_disabled_switch_still_returns_none(self, prod, monkeypatch):
        from backend.customer_service.graph_builder import _build_checkpointer

        monkeypatch.setattr(
            "backend.config.customer_service.CS_CHECKPOINTER_ENABLED", False)
        assert _build_checkpointer() is None


class TestConsumersShareOnePolicy:
    """防回退：两个消费方必须调用判据，不得再写裸 warning 降级。"""

    @pytest.mark.parametrize(
        "rel",
        ["orchestration/graph/checkpointer.py", "customer_service/graph_builder.py"],
    )
    def test_consumer_calls_degrade_or_raise(self, rel):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")
        assert "degrade_or_raise" in src, f"{rel} 没走统一判据"
        assert "falling back to MemorySaver" not in src, (
            f"{rel} 回退到自持的降级告警（应调用 config.checkpointer.degrade_or_raise）"
        )


# ============================================================
# 三、边界：旅游域有意不接入
# ============================================================
class TestTravelBoundary:
    def test_travel_discloses_degradation_instead_of_failing(self):
        """旅游域把降级作为状态上浮（PERSISTENCE_DEGRADED），全链路可见 → 不硬失败。

        若哪天旅游域也改成硬失败或改成静默降级，这个用例会红，提醒先对齐口径。
        """
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1]
               / "travel" / "graph_builder.py").read_text(encoding="utf-8")
        assert "PERSISTENCE_DEGRADED" in src
        assert "degrade_or_raise" not in src, (
            "旅游域若接入统一判据，需同步解释它「状态上浮」的披露语义如何保留"
        )
