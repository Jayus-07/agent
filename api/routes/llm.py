"""
api/routes/llm.py — LLM 切换 + 余额查询 API

端点:
  GET  /llm/models    — 列出可用模型
  GET  /llm/current   — 获取当前模型
  POST /llm/switch    — 切换当前模型
  GET  /llm/balance   — 查询余额（默认查当前 provider，可指定）
"""
from fastapi import APIRouter
from pydantic import BaseModel

from llm.llm_factory import get_llm_factory
from llm.models import AVAILABLE_MODELS
from utils.logger import logger

router = APIRouter(prefix="/llm", tags=["llm"])


# =====================================================
# Schema
# =====================================================

class SwitchRequest(BaseModel):
    model: str  # 形如 "qwen2.5:3b" / "deepseek-chat"


# =====================================================
# 端点
# =====================================================

@router.get("/models")
async def list_models():
    """列出所有可用 LLM 模型"""
    factory = get_llm_factory()
    return {
        "models": AVAILABLE_MODELS,
        "current": factory.get_current_model_name(),
    }


@router.get("/current")
async def get_current():
    """获取当前生效的模型"""
    factory = get_llm_factory()
    name = factory.get_current_model_name()
    provider = factory._get_provider(name)
    return {"model": name, "provider": provider}


@router.post("/switch")
async def switch_model(req: SwitchRequest):
    """切换全局当前模型

    body: {"model": "qwen2.5:3b" | "deepseek-chat" | "deepseek-reasoner"}

    200: {"ok": true, "model": "...", "provider": "..."}
    400: {"ok": false, "error": "未知模型"}
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

    query: provider=deepseek  (可选，不传则查当前模型所在 provider)

    200: {"ok": true, "provider": "...", "balance": "...", "currency": "..."}
    503: {"ok": false, "error": "API Key 未配置" / "请求失败"}
    """
    factory = get_llm_factory()
    result = factory.get_balance(provider)
    if not result.get("ok"):
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=result)
    return result
