"""pipeline 初始化期间 API 不得冻结 — P0 回归测试。

背景：RAGPipeline() 构造含全量增量同步（本地环境可达数十分钟），期间持
_pipeline_lock。此前 deps.get_rag_status() 在事件循环线程同步等待该锁，
导致初始化期间整个 FastAPI API 冻结（连 /rag/health 都超时）。

本文件锁定修复后的行为：
  1. get_rag_pipeline_state() 四种状态且永不阻塞
  2. 锁被占用（初始化进行中）时 get_rag_status() 立即返回 initializing
  3. require_rag_ready() 未就绪时快速抛 503
"""
import time

import pytest

import backend.rag.pipeline as pipeline_mod
from backend.app.api import deps


@pytest.fixture()
def clean_pipeline_state(monkeypatch):
    """将 pipeline 单例模块级状态重置为初始值，测试后自动还原。"""
    monkeypatch.setattr(pipeline_mod, "_pipeline_singleton", None, raising=False)
    monkeypatch.setattr(pipeline_mod, "_pipeline_init_error", None, raising=False)
    monkeypatch.setattr(pipeline_mod, "_pipeline_initializing", False, raising=False)
    yield


class TestGetRagPipelineState:
    def test_not_started(self, clean_pipeline_state):
        assert pipeline_mod.get_rag_pipeline_state() == {"state": "not_started"}

    def test_initializing(self, clean_pipeline_state, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "_pipeline_initializing", True)
        state = pipeline_mod.get_rag_pipeline_state()
        assert state["state"] == "initializing"
        assert "message" in state

    def test_ready(self, clean_pipeline_state, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "_pipeline_singleton", object())
        assert pipeline_mod.get_rag_pipeline_state() == {"state": "ready"}

    def test_error(self, clean_pipeline_state, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "_pipeline_init_error", "boom")
        state = pipeline_mod.get_rag_pipeline_state()
        assert state["state"] == "error"
        assert state["error"] == "boom"

    def test_ready_takes_precedence_over_error_flag(self, clean_pipeline_state, monkeypatch):
        """单例已存在时即使残留 error 文案也应报 ready。"""
        monkeypatch.setattr(pipeline_mod, "_pipeline_singleton", object())
        monkeypatch.setattr(pipeline_mod, "_pipeline_init_error", "stale")
        assert pipeline_mod.get_rag_pipeline_state()["state"] == "ready"


class TestGetRagStatusNonBlocking:
    def test_status_returns_immediately_while_lock_held(self, clean_pipeline_state):
        """初始化线程持锁期间，get_rag_status() 必须立即返回而非等锁。

        旧实现（_ensure_pipeline_ref → get_rag_pipeline）在此场景会死等
        _pipeline_lock —— 若本用例挂起即回归。
        """
        pipeline_mod._pipeline_lock.acquire()
        pipeline_mod._pipeline_initializing = True
        try:
            t0 = time.time()
            status = deps.get_rag_status()
            elapsed = time.time() - t0
            assert elapsed < 1.0, f"get_rag_status 阻塞了 {elapsed:.1f}s（回归：不得等初始化锁）"
            assert status == {
                "ready": False,
                "status": "initializing",
                "message": "RAG 管道初始化中（含索引同步），请稍后重试",
            }
        finally:
            pipeline_mod._pipeline_lock.release()

    def test_status_ready(self, clean_pipeline_state, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "_pipeline_singleton", object())
        assert deps.get_rag_status() == {"ready": True, "status": "ready"}

    def test_status_error(self, clean_pipeline_state, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "_pipeline_init_error", "init failed")
        status = deps.get_rag_status()
        assert status["ready"] is False
        assert status["status"] == "error"
        assert status["error"] == "init failed"

    def test_not_started_kicks_background_init(self, clean_pipeline_state, monkeypatch):
        """not_started 首次访问应触发后台初始化（且不阻塞当前线程）。"""
        kicked = []
        monkeypatch.setattr(deps, "_kick_pipeline_init", lambda: kicked.append(True))
        status = deps.get_rag_status()
        assert kicked == [True]
        assert status["ready"] is False
        assert status["status"] == "initializing"

    def test_kick_is_idempotent_when_initializing(self, clean_pipeline_state, monkeypatch):
        """已在初始化中时 _kick_pipeline_init 不得再起线程。"""
        monkeypatch.setattr(pipeline_mod, "_pipeline_initializing", True)
        started = []
        monkeypatch.setattr(
            deps.threading, "Thread",
            lambda *a, **kw: started.append(1) or type("T", (), {"start": lambda self: None})(),
        )
        deps._kick_pipeline_init()
        assert started == []


class TestRequireRagReady:
    def test_raises_503_while_initializing(self, clean_pipeline_state, monkeypatch):
        from fastapi import HTTPException
        monkeypatch.setattr(pipeline_mod, "_pipeline_initializing", True)
        with pytest.raises(HTTPException) as exc_info:
            deps.require_rag_ready()
        assert exc_info.value.status_code == 503
        detail = exc_info.value.detail
        assert detail["code"] == "SERVICE_NOT_READY"
        assert detail["status"] == "initializing"

    def test_passes_when_ready(self, clean_pipeline_state, monkeypatch):
        monkeypatch.setattr(pipeline_mod, "_pipeline_singleton", object())
        deps.require_rag_ready()  # 不应抛异常
