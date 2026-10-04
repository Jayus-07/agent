"""
api/routes/llm.py — LLM 切换 + 余额查询 API

端点:
  GET  /llm/models    — 列出可用模型
  GET  /llm/current   — 获取当前模型
  POST /llm/switch    — 切换当前模型
  GET  /llm/balance   — 查询余额（默认查当前 provider，可指定）
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.app.api.deps import require_admin_user
from backend.config import llm as config_llm
from backend.infra.llm import get_llm_factory
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod
from backend.infra.llm.models import get_available_models
from backend.shared.logger import logger

router = APIRouter(prefix="/llm", tags=["llm"])


def _current_runtime_model(factory) -> str:
    """返回管理端 DB 角色解析后的主模型，避免旧 Factory/env 污染用户端展示。

    `get_current_model_name()` 保留给历史内存切换兼容路径；对外的模型目录和
    当前模型接口必须与 `model_roles` 的 DB 覆盖语义一致，否则用户端会显示
    一个不在当前注册表中的旧模型。
    """
    try:
        from backend.config import model_roles

        value = model_roles.resolve_effective("main").get("value")
        if value:
            return str(value)
    except Exception:
        logger.warning("[LLM] 读取 main 角色失败，回退历史 Factory 当前模型", exc_info=True)
    return factory.get_current_model_name()


# =====================================================
# Schema
# =====================================================

class SwitchRequest(BaseModel):
    model: str


class MQSwitchRequest(BaseModel):
    mode: str  # "auto" | "on" | "off"


# =====================================================
# MultiQuery 模式
# =====================================================

@router.get("/multiquery")
async def get_multiquery_mode():
    from backend.rag.retrieval.multi_query import get_mq_mode
    return {"mode": get_mq_mode()}


@router.post("/multiquery")
async def set_multiquery_mode(req: MQSwitchRequest):
    if req.mode not in ("auto", "on", "always", "off"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="mode 必须为 auto/on/off")
    from backend.rag.retrieval.multi_query import set_mq_mode
    set_mq_mode(req.mode)
    return {"ok": True, "mode": req.mode}


# =====================================================
# 端点

@router.get("/models")
async def list_models():
    """列出所有可用 LLM 模型"""
    factory = get_llm_factory()
    models = []
    for model in get_available_models():
        item = dict(model)
        item["modelKind"] = models_mod.normalize_model_kind(item.get("model_kind"))
        provider = str(item.get("provider") or "")
        if provider == "ollama" and not config_llm.OLLAMA_ENABLED:
            item["available"] = False
            item["availabilityReason"] = "Ollama 当前未启用（cloud 模式禁用本地模型）"
        else:
            reason = credentials_mod.check_provider_usable(provider)
            item["available"] = reason is None
            item["availabilityReason"] = reason
        models.append(item)
    return {
        "models": models,
        "current": _current_runtime_model(factory),
    }


@router.get("/current")
async def get_current():
    """获取当前生效的模型"""
    factory = get_llm_factory()
    name = _current_runtime_model(factory)
    provider = factory._get_provider(name)
    return {"model": name, "provider": provider}


@router.post("/switch")
async def switch_model(req: SwitchRequest, _admin=Depends(require_admin_user)):
    """切换全局当前模型（仅管理员）

    全局默认影响所有用户，故门禁收敛到 admin（2026-09-19，B.9 决策②）：
    非管理员的模型切换改走**会话级覆盖**（POST /chat 带 model 字段，仅本次会话生效），
    不再影响全局。set_current 的内存语义保留，作为 admin 的调试通道。

    body: {"model": "doubao-seed-2.0-mini" | "qwen3.8-flash"}

    200: {"ok": true, "model": "...", "provider": "..."}
    400: {"ok": false, "error": "未知模型"}
    403: 非管理员（SENSITIVE_API_GUARD_MODE=enforce 时）
    503: {"ok": false, "error": "DEEPSEEK_API_KEY 未配置"}
    """
    factory = get_llm_factory()
    result = factory.set_current(req.model)
    if not result.get("ok"):
        # 400/503 区分：模型未知 400，配置缺失 503
        status_code = 400 if "未知模型" in result.get("error", "") else 503
        from fastapi import HTTPException
        raise HTTPException(status_code=status_code, detail=result)
    return result


@router.get("/balance")
async def get_balance(provider: str = None):
    """查询 provider 余额

    query: provider=qwen  (可选，不传则查当前模型所在 provider)

    200: {"ok": true, "provider": "...", "balance": "...", "currency": "..."}
    503: {"ok": false, "error": "API Key 未配置" / "请求失败"}
    """
    factory = get_llm_factory()
    # P1-16: 异步查询余额，不再阻塞事件循环（原同步 requests 最长挂 10s）
    result = await factory.get_balance_async(provider)
    if not result.get("ok"):
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=result)
    return result
