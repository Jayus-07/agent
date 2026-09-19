"""客服系统测试 fixtures

Phase 2 测试基础设施：
  - CS_ENABLED 开关切换（模块级求值，需 monkeypatch 模块属性）
  - DB 连接 mock（单元测试不依赖真实 PostgreSQL）
  - LLM 响应 mock（隔离外部 LLM 调用）
  - 空知识库场景处理
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.customer_service.errors import CustomerServiceError

# ── 基础 fixtures ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _supervisor_llm_off_by_default(monkeypatch):
    """CS Supervisor Layer3 LLM 决策默认关闭（测试确定性，P1 2026-09-19）。

    开关默认 true 且开发环境带 DeepSeek key——低置信用例会打出真实 LLM
    调用（800ms 超时内可达时），expert 派发随模型回答漂移（实测：
    knowledge 之后被派发 query/action/handoff/complaint 直至循环上限，
    断言 expert_history 的用例随机挂）。
    需要覆盖 LLM 层的用例自行 patch 该开关为 True——测试级 patch 晚于
    本 fixture 应用，可正常覆盖（test_cs_supervisor.py 即此模式）。
    """
    import backend.config.customer_service as cs_config

    monkeypatch.setattr(cs_config, "CS_SUPERVISOR_LLM_ENABLED", False)


@pytest.fixture
def sample_conversation_id():
    return "conv-test-001"


@pytest.fixture
def sample_user_id():
    return "user-test-001"


@pytest.fixture
def sample_trace_id():
    return "trace-test-001"


# ── Issue #2: CS_ENABLED 开关切换 ─────────────────────────────
# CS_ENABLED 在 config 模块导入时求值（os.getenv → bool），
# 测试中需 monkeypatch 模块属性而非环境变量。

@pytest.fixture
def cs_enabled(monkeypatch):
    """强制开启 CS_ENABLED（测试 CS 路由/功能时需要）。"""
    import backend.config as cfg_mod
    import backend.config.customer_service as cs_mod
    monkeypatch.setattr(cs_mod, "CS_ENABLED", True)
    monkeypatch.setattr(cfg_mod, "CS_ENABLED", True)
    return True


@pytest.fixture
def cs_disabled(monkeypatch):
    """强制关闭 CS_ENABLED（测试降级/旁路时需要）。"""
    import backend.config as cfg_mod
    import backend.config.customer_service as cs_mod
    monkeypatch.setattr(cs_mod, "CS_ENABLED", False)
    monkeypatch.setattr(cfg_mod, "CS_ENABLED", False)
    return False


# ── Issue #1 & #4: DB 连接 mock ───────────────────────────────
# Phase 2 的 router / knowledge Q&A 可能触及 DB，
# 单元测试不应依赖真实 PostgreSQL 实例。

@pytest.fixture
def mock_db_pool():
    """模拟 asyncpg connection pool，返回 MagicMock。

    用法:
        async def test_xxx(self, mock_db_pool):
            mock_db_pool.fetchrow.return_value = {"status": "active"}
            ...
    """
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock(return_value="OK")
    return pool


@pytest.fixture
def mock_cs_db(mock_db_pool, monkeypatch):
    """将客服 DB 操作替换为 mock pool。

    如果 Phase 2 代码有专门的 DB 模块（如 customer_service.db），
    在此 monkeypatch 其 get_pool / get_connection。
    """
    monkeypatch.setattr(
        "backend.customer_service.db.get_pool",
        lambda: mock_db_pool,
        raising=False,
    )
    return mock_db_pool


# ── Issue #4: LLM 响应 mock ──────────────────────────────────

@pytest.fixture
def mock_llm_response():
    """返回可配置的 LLM mock 工厂。

    用法:
        def test_router(self, mock_llm_response):
            mock_llm_response("KNOWLEDGE")  # 设置 LLM 返回该意图
    """
    mock = MagicMock()

    def _configure(intent: str = "KNOWLEDGE", confidence: float = 0.9):
        mock.return_value = {
            "intent": intent,
            "confidence": confidence,
            "domain": intent,
        }
        return mock

    _configure("KNOWLEDGE")
    return _configure


@pytest.fixture
def mock_rag_chain():
    """模拟 RAGChain.ask()，返回可配置的检索回答。"""
    chain = AsyncMock()

    def _configure(answer: str = "这是测试回答。", sources: list | None = None):
        chain.ask.return_value = answer
        chain._last_sources = sources or []
        chain._last_meta = {"confidence": 0.85, "can_answer": True}
        return chain

    _configure()
    return _configure


# ── Issue #5: 空知识库处理 ────────────────────────────────────

@pytest.fixture
def empty_knowledge_bases(monkeypatch):
    """将 CS_KNOWLEDGE_BASES 置空，测试空 KB 降级行为。"""
    import backend.config.customer_service as cs_mod
    monkeypatch.setattr(cs_mod, "CS_KNOWLEDGE_BASES", {})
    return {}


@pytest.fixture
def single_kb(monkeypatch):
    """只保留一个知识库，测试单 KB 场景。"""
    import backend.config.customer_service as cs_mod
    kb = {"cs_faq": {"name": "客服FAQ", "description": "常见问题"}}
    monkeypatch.setattr(cs_mod, "CS_KNOWLEDGE_BASES", kb)
    return kb


# ── 通用断言辅助 ─────────────────────────────────────────────

@pytest.fixture
def assert_cs_error():
    """断言上下文是 CustomerServiceError 的辅助工厂。"""
    def _check(exc: Exception, expected_code: str | None = None):
        assert isinstance(exc, CustomerServiceError)
        if expected_code:
            assert exc.code == expected_code
    return _check
