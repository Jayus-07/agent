"""模型配置治理写入、历史与漂移 API。

读取清单与供应商探测分别由 ``sys_model_roles`` / ``sys_providers`` 提供；
本模块集中承载同一业务域的写入和派生视图，保持前端单域 API 的边界。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.deps import (
    OperatorIdentity,
    require_admin_user,
    require_idempotency_key,
    require_user_actor,
)
from backend.services.model_config import (
    HISTORY_OBJECTS,
    ModelConfigConflict,
    ModelConfigNotFound,
    get_model_config_service,
)
from backend.shared.logger import logger


router = APIRouter(tags=["模型配置治理"])


class _ModelConfigUserError(HTTPException):
    """模型配置错误的安全文案，允许全局协议保留可操作原因。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.user_message = message


class ModelRoleUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # eval_gen 允许保存空值，表示明确停用评测生成；其它角色仍由 service 校验非空。
    model_name: str = Field(..., alias="modelName", min_length=0, max_length=256)


class ProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str = Field(..., alias="displayName", min_length=1, max_length=128)
    driver: Literal["openai", "anthropic", "ollama", "specialized"]
    base_url: str = Field("", alias="baseUrl", max_length=2048)
    network_scope: Literal["public", "private"] = Field(
        "public", alias="networkScope"
    )
    billing: Literal["metered", "subscription", "local"] = "metered"
    enabled: bool = True
    model_name: str | None = Field(None, alias="modelName", max_length=256)
    model_kind: Literal["chat", "embedding", "rerank", "vision", "speech"] | None = Field(
        None, alias="modelKind"
    )
    extra_headers: dict[str, str] = Field(default_factory=dict, alias="extraHeaders")
    # None = 不改；非空 = 轮换；clearApiKey=true = 删除。
    api_key: str | None = Field(None, alias="apiKey", max_length=4096)
    clear_api_key: bool = Field(False, alias="clearApiKey")


class ProviderCreateRequest(BaseModel):
    """自定义供应商的最小配置：地址、密钥和模型名。"""

    model_config = ConfigDict(populate_by_name=True)

    display_name: str = Field("", alias="displayName", max_length=128)
    driver: Literal["openai", "anthropic", "ollama"] = "openai"
    base_url: str = Field(..., alias="baseUrl", min_length=1, max_length=2048)
    network_scope: Literal["public", "private"] = Field(
        "public", alias="networkScope"
    )
    billing: Literal["metered", "subscription", "local"] = "metered"
    enabled: bool = True
    model_name: str = Field(..., alias="modelName", min_length=1, max_length=256)
    model_kind: Literal["chat", "embedding", "rerank", "vision", "speech"] = Field(
        "chat", alias="modelKind"
    )
    api_key: str = Field(..., alias="apiKey", min_length=1, max_length=4096)
    extra_headers: dict[str, str] = Field(default_factory=dict, alias="extraHeaders")


class ProviderModelCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_name: str = Field(..., alias="modelName", min_length=1, max_length=256)
    model_kind: Literal["chat", "embedding", "rerank", "vision", "speech"] = Field(
        "chat", alias="modelKind"
    )


class SpecializedProviderRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str = Field(..., alias="displayName", min_length=1, max_length=128)
    provider_id: str | None = Field(None, alias="providerId", max_length=128)
    base_url: str = Field(..., alias="baseUrl", min_length=1, max_length=2048)
    api_key: str | None = Field(None, alias="apiKey", max_length=4096)
    network_scope: Literal["public", "private"] = Field(
        "public", alias="networkScope"
    )
    billing: Literal["metered", "subscription", "local"] = "metered"


class SpecializedBindingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_name: str = Field(..., alias="modelName", min_length=1, max_length=256)
    adapter: str = Field(..., min_length=1, max_length=64)
    base_url: str = Field(..., alias="baseUrl", min_length=1, max_length=2048)
    options: dict[str, Any] = Field(default_factory=dict)


class SpecializedConfigureRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: SpecializedProviderRequest
    bindings: dict[str, SpecializedBindingRequest]


def _service(request: Request):
    return getattr(request.app.state, "model_config_service", None) or get_model_config_service()


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ModelConfigNotFound):
        return _ModelConfigUserError(status_code=404, message=str(exc))
    if isinstance(exc, ModelConfigConflict):
        return _ModelConfigUserError(status_code=409, message=str(exc))
    if isinstance(exc, ValueError):
        return _ModelConfigUserError(status_code=422, message=str(exc))
    logger.exception(
        "[ModelConfig] 配置写入失败 type=%s message=%s",
        type(exc).__name__,
        str(exc)[:300],
    )
    return HTTPException(status_code=503, detail="模型配置存储暂不可用")


@router.put("/sys/model-roles/{role}")
async def update_model_role(
    role: str,
    body: ModelRoleUpdateRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    require_idempotency_key(request)
    try:
        return await _service(request).update_role(
            role, body.model_name, operator.actor
        )
    except Exception as exc:  # 统一把存储异常转为可操作的 5xx
        raise _error(exc) from exc


@router.put("/sys/providers/{provider_id}")
async def update_provider(
    provider_id: str,
    body: ProviderUpdateRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    require_idempotency_key(request)
    try:
        return await _service(request).update_provider(
            provider_id, body.model_dump(by_alias=True), operator.actor
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/sys/providers")
async def create_provider(
    body: ProviderCreateRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    """创建自定义供应商并注册一个模型。"""
    require_idempotency_key(request)
    try:
        return await _service(request).create_provider(
            body.model_dump(by_alias=True), operator.actor
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/sys/providers/{provider_id}/models")
async def add_provider_model(
    provider_id: str,
    body: ProviderModelCreateRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    """测试通过后向已有供应商追加模型。"""
    require_idempotency_key(request)
    try:
        return await _service(request).add_provider_model(
            provider_id, body.model_dump(by_alias=True), operator.actor
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.delete("/sys/providers/{provider_id}/models")
async def remove_provider_model(
    provider_id: str,
    request: Request,
    model_name: str = Query(..., alias="modelName"),
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    """移除供应商下的自建模型条目。

    模型名走 **query 参数**而非路径段：`Qwen/Qwen3-32B`、`BAAI/bge-m3` 这类名字
    自带斜杠，放进路径会被拆成多段而匹配不到 `{model_name}`。
    """
    require_idempotency_key(request)
    try:
        return await _service(request).remove_provider_model(
            provider_id, model_name, operator.actor
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/sys/providers/{provider_id}/models")
async def list_provider_models(
    provider_id: str,
    request: Request,
    model_kind: Literal["chat", "embedding", "rerank", "vision", "speech"] | None = Query(
        None, alias="modelKind"
    ),
    operator: OperatorIdentity = Depends(require_user_actor),
) -> dict[str, Any]:
    del operator
    try:
        return await _service(request).list_provider_models(provider_id, model_kind)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/sys/specialized-models")
async def list_specialized_models(
    request: Request,
    operator: OperatorIdentity = Depends(require_user_actor),
) -> dict[str, Any]:
    del operator
    try:
        return await _service(request).list_specialized()
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/sys/specialized-models/test-and-save")
async def configure_specialized_models(
    body: SpecializedConfigureRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    require_idempotency_key(request)
    try:
        return await _service(request).configure_specialized(
            body.model_dump(by_alias=True), operator.actor
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/sys/config/history")
async def list_config_history(
    request: Request,
    object_type: str | None = Query(None, alias="object"),
    limit: int = Query(200, ge=1, le=1000),
    operator: OperatorIdentity = Depends(require_user_actor),
) -> dict[str, Any]:
    del operator
    if object_type and object_type not in HISTORY_OBJECTS:
        raise HTTPException(status_code=422, detail="object 类型非法")
    try:
        return await _service(request).list_history(object_type, limit)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/sys/config/history/{history_id}/rollback")
async def rollback_config_history(
    history_id: int,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    require_idempotency_key(request)
    try:
        return await _service(request).rollback(history_id, operator.actor)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/sys/config/drift")
async def get_config_drift(
    request: Request,
    operator: OperatorIdentity = Depends(require_user_actor),
) -> dict[str, Any]:
    del operator
    try:
        return await _service(request).drift()
    except Exception as exc:
        raise _error(exc) from exc
