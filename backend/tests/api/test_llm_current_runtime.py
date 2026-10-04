"""验证用户端模型展示与管理端 DB 角色绑定使用同一运行时来源。"""

from types import SimpleNamespace

from backend.app.api.routes import llm


def test_current_runtime_model_prefers_db_main_role(monkeypatch):
    factory = SimpleNamespace(get_current_model_name=lambda: "stale-env-model")
    monkeypatch.setattr(
        "backend.config.model_roles.resolve_effective",
        lambda role: {"role": role, "value": "doubao-seed-2.0-mini", "source": "db"},
    )

    assert llm._current_runtime_model(factory) == "doubao-seed-2.0-mini"


def test_current_runtime_model_falls_back_when_db_role_unavailable(monkeypatch):
    factory = SimpleNamespace(get_current_model_name=lambda: "compat-model")
    monkeypatch.setattr(
        "backend.config.model_roles.resolve_effective",
        lambda role: {"role": role, "value": "", "source": "default"},
    )

    assert llm._current_runtime_model(factory) == "compat-model"
