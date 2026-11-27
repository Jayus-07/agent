"""架构守卫测试：验证 backend/services/model_config 的物理模块化解耦与兼容性契约。"""
import inspect
from pathlib import Path

import pytest

import backend.services.model_config as model_config_facade
from backend.services.model_config import (
    HISTORY_OBJECTS,
    ModelConfigConflict,
    ModelConfigNotFound,
    ModelConfigService,
    get_model_config_service,
)
from backend.services.model_governance.base import DependencyProxy


def test_model_config_facade_line_count_within_budget() -> None:
    """守卫门面文件大小，防止代码重新膨胀（阈值 250 行）。"""
    facade_path = Path(model_config_facade.__file__)
    lines = facade_path.read_text(encoding="utf-8").splitlines()
    assert (
        len(lines) < 250
    ), f"model_config.py 门面行数超标（当前 {len(lines)} 行，应小于 250 行）"


def test_model_config_submodules_line_count_within_budget() -> None:
    """守卫各个领域子模块大小，遵循单一职责原则（每个子模块均小于 1000 行）。"""
    pkg_dir = Path(model_config_facade.__file__).parent / "model_governance"
    assert pkg_dir.is_dir(), f"{pkg_dir} 应该是一个包目录"

    py_files = list(pkg_dir.glob("*.py"))
    assert len(py_files) >= 8, f"至少应有 8 个子模块文件，当前发现 {len(py_files)} 个"

    for py_file in py_files:
        lines = py_file.read_text(encoding="utf-8").splitlines()
        assert (
            len(lines) < 1000
        ), f"子模块 {py_file.name} 行数超标（当前 {len(lines)} 行，应小于 1000 行）"


def test_model_config_service_methods_integrity() -> None:
    """验证 ModelConfigService 保留了全部 21 个核心业务方法。"""
    service = ModelConfigService()
    expected_methods = {
        "preview_embedding_change",
        "update_role",
        "_validate_policy_payload",
        "get_role_policy",
        "update_role_policy",
        "list_model_health",
        "get_index_compat",
        "_read_provider_secret",
        "configure_specialized",
        "list_specialized",
        "update_provider",
        "_upsert_model",
        "list_provider_models",
        "add_provider_model",
        "remove_provider_model",
        "create_provider",
        "delete_provider",
        "record_probe",
        "list_history",
        "rollback",
        "drift",
    }
    for method_name in expected_methods:
        assert hasattr(service, method_name), f"ModelConfigService 缺失方法 {method_name}"
        assert callable(
            getattr(service, method_name)
        ), f"ModelConfigService.{method_name} 应为可调用对象"


def test_model_config_facade_exports_and_monkeypatch_proxy(monkeypatch) -> None:
    """验证门面导出与 DependencyProxy 的透明代理能力。"""
    # 验证异常定义
    assert issubclass(ModelConfigNotFound, LookupError)
    assert issubclass(ModelConfigConflict, RuntimeError)

    # 验证核心常量
    assert "role" in HISTORY_OBJECTS
    assert hasattr(model_config_facade, "_COMPONENT_BY_MODEL_KIND")

    # 验证单例方法
    svc1 = get_model_config_service()
    svc2 = get_model_config_service()
    assert svc1 is svc2
    assert isinstance(svc1, ModelConfigService)

    # 验证 DependencyProxy 在 monkeypatch 下的穿透行为
    proxy = DependencyProxy("test_symbol", fallback="default_val")
    assert proxy._target() == "default_val"

    monkeypatch.setattr(model_config_facade, "test_symbol", "patched_val", raising=False)
    assert proxy._target() == "patched_val"
