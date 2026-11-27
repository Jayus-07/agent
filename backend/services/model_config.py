"""模型与供应商配置治理服务（统一门面 Facade）。

本模块是管理端模型配置的唯一写入口。本门面保留与历史单文件完全一致的公共导出、
模块级依赖符号和类结构，底层能力已解耦并下沉至 model_governance 领域子模块：
- base: 异常、常量、数据清洗与依赖代理
- compatibility: 向量索引维度兼容性与 Embedding 变更预检
- roles: 角色模型绑定、Fallback 解析与运行策略
- specialized: 专精模型（Embedding / Rerank 专用通道）配置
- providers: 供应商元数据、凭据加密与网络范围
- models: 模型目录、单价校验落库与能力标签管理
- health: 模型健康巡检、人工状态置失效与探测记录
- history: 变更历史审计、快照回滚与配置漂移分析
"""
from __future__ import annotations

# ==============================================================================
# 1. 显式保留被存量测试与运行时 monkeypatch 的模块级依赖符号
# ==============================================================================
from backend.config import model_roles
from backend.config.llm import OLLAMA_ENABLED
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod
from backend.infra.llm import registry_store
from backend.memory.database import get_session
from backend.services import (
    provider_probe,
    specialized_model_adapters,
    specialized_model_probe,
)
from backend.shared.crypto import decrypt_secret, encrypt_secret, fingerprint, last4
from backend.shared.logger import logger

# ==============================================================================
# 2. 导出所有领域子模块沉淀的公共异常、常量、辅助函数与服务实现
# ==============================================================================
from backend.services.model_governance import (
    HISTORY_OBJECTS,
    ModelConfigConflict,
    ModelConfigNotFound,
    ModelConfigService,
    _COMPONENT_BY_MODEL_KIND,
    _EXTRA_BODY_MAX_DEPTH,
    _EXTRA_BODY_MAX_KEYS,
    _FORBIDDEN_EXTRA_BODY_KEYS,
    _PROBE_SAVE_RETRY_BACKOFF,
    _SPECIALIZED_ROLES,
    DependencyProxy,
    _apply_model_pricing,
    _decode_json_object,
    _extra_body_depth,
    _has_effective_price,
    _invalidate_manual_health,
    _iso,
    _json_value,
    _model_validation_issue,
    _normalize_extra_body,
    _normalize_extra_headers,
    _parse_model_price,
    _provider_payload,
    _provider_slug,
    _specialized_binding_payload,
    _validate_model_pricing,
    _valid_base_url,
    _worst_probe_grade,
)

# ==============================================================================
# 3. 门面单例管理
# ==============================================================================
_service: ModelConfigService | None = None


def get_model_config_service() -> ModelConfigService:
    global _service
    if _service is None:
        _service = ModelConfigService()
    return _service


__all__ = [
    "HISTORY_OBJECTS",
    "ModelConfigConflict",
    "ModelConfigNotFound",
    "ModelConfigService",
    "OLLAMA_ENABLED",
    "_COMPONENT_BY_MODEL_KIND",
    "_EXTRA_BODY_MAX_DEPTH",
    "_EXTRA_BODY_MAX_KEYS",
    "_FORBIDDEN_EXTRA_BODY_KEYS",
    "_PROBE_SAVE_RETRY_BACKOFF",
    "_SPECIALIZED_ROLES",
    "DependencyProxy",
    "_apply_model_pricing",
    "_decode_json_object",
    "_extra_body_depth",
    "_has_effective_price",
    "_invalidate_manual_health",
    "_iso",
    "_json_value",
    "_model_validation_issue",
    "_normalize_extra_body",
    "_normalize_extra_headers",
    "_parse_model_price",
    "_provider_payload",
    "_provider_slug",
    "_specialized_binding_payload",
    "_validate_model_pricing",
    "_valid_base_url",
    "_worst_probe_grade",
    "credentials_mod",
    "decrypt_secret",
    "encrypt_secret",
    "fingerprint",
    "get_model_config_service",
    "get_session",
    "last4",
    "logger",
    "model_roles",
    "models_mod",
    "provider_probe",
    "registry_store",
    "specialized_model_adapters",
    "specialized_model_probe",
]
