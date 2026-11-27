"""模型治理中心领域服务包（Model Governance Domain Services）。"""
from __future__ import annotations

from backend.services.model_governance.base import (
    HISTORY_OBJECTS,
    ModelConfigConflict,
    ModelConfigNotFound,
    _COMPONENT_BY_MODEL_KIND,
    _EXTRA_BODY_MAX_DEPTH,
    _EXTRA_BODY_MAX_KEYS,
    _FORBIDDEN_EXTRA_BODY_KEYS,
    _PROBE_SAVE_RETRY_BACKOFF,
    _SPECIALIZED_ROLES,
    DependencyProxy,
    _decode_json_object,
    _extra_body_depth,
    _iso,
    _json_value,
    _normalize_extra_body,
    _normalize_extra_headers,
    _provider_slug,
    _valid_base_url,
)
from backend.services.model_governance.compatibility import CompatibilityMixin
from backend.services.model_governance.health import (
    HealthMixin,
    _invalidate_manual_health,
    _worst_probe_grade,
)
from backend.services.model_governance.history import HistoryMixin
from backend.services.model_governance.models import (
    ModelCatalogMixin,
    _apply_model_pricing,
    _has_effective_price,
    _model_validation_issue,
    _parse_model_price,
    _validate_model_pricing,
)
from backend.services.model_governance.providers import ProviderMixin, _provider_payload
from backend.services.model_governance.roles import RoleConfigMixin
from backend.services.model_governance.specialized import (
    SpecializedMixin,
    _specialized_binding_payload,
)


class ModelConfigService(
    RoleConfigMixin,
    CompatibilityMixin,
    HealthMixin,
    SpecializedMixin,
    ProviderMixin,
    ModelCatalogMixin,
    HistoryMixin,
):
    """模型治理中心统一聚合服务实现。"""


__all__ = [
    "HISTORY_OBJECTS",
    "ModelConfigConflict",
    "ModelConfigNotFound",
    "ModelConfigService",
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
]
