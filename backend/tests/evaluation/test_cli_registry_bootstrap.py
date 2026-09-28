"""评估 CLI 启动时加载数据库模型注册表的回归测试。"""

from backend.evaluation import cli


def test_bootstrap_llm_registry_refreshes_database_overrides(monkeypatch):
    """独立评估进程必须在构造 RAGPipeline 前刷新 DB 模型与凭据覆盖层。"""
    calls: list[str] = []

    async def fake_refresh_registry():
        calls.append("refresh")

    monkeypatch.setattr(
        "backend.infra.llm.registry_store.refresh_registry",
        fake_refresh_registry,
    )

    cli._bootstrap_llm_registry()

    assert calls == ["refresh"]
