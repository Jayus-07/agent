"""siliconflow.py — SiliconFlow Provider（硅基流动，兼容 OpenAI 协议）

提供:
  - build_siliconflow(): 构建 ChatOpenAI 实例（api.siliconflow.cn）
  - get_siliconflow_balance(): 查询账户余额（/v1/user/info）

模型名沿用硅基流动的 org/model 形式（如 Qwen/Qwen3-8B），
embedding 与 rerank 也走同一 Key（EMBEDDING_API_KEY 与本 Key 同源）。

凭据在**调用时**解析（credentials，见 infra/llm/credentials.py）。
"""

from backend.config import (
    LLM_CONTEXT_LENGTH,
    LLM_REQUEST_TIMEOUT,
    LLM_STREAM_USAGE,
    LLM_TEMPERATURE,
    SILICONFLOW_API_BASE,
    SILICONFLOW_API_KEY,
)
from backend.infra.llm.credentials import ProviderCredentials
from backend.shared.logger import logger


def build_siliconflow(
    model_name: str, credentials: ProviderCredentials | None = None
) -> object:
    """构建 SiliconFlow 模型实例（通过 OpenAI 兼容协议）"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ImportError(
            "siliconflow provider 需要 langchain_openai 包，请 pip install langchain-openai"
        ) from e

    # Qwen3 系是思考混合模型：硅基流动默认开思考（实测问答 47s/297 字思考），
    # 本平台的结构化任务（路由/抽取/计划/报告）不需要推理链——默认关思考
    # （对齐 qwen_tp 的 QWEN_ENABLE_THINKING 模式），需要思考链时显式开
    import os
    enable_thinking = os.getenv(
        "SILICONFLOW_ENABLE_THINKING", "false"
    ).strip().lower() in ("1", "true", "yes")

    api_key = (credentials.api_key if credentials else None) or SILICONFLOW_API_KEY
    base_url = (credentials.base_url if credentials else None) or SILICONFLOW_API_BASE

    body = {"enable_thinking": enable_thinking}
    if credentials and credentials.extra_body:
        body.update(credentials.extra_body)

    return ChatOpenAI(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
        api_key=api_key,
        base_url=base_url,
        extra_body=body,
        # SiliconFlow 兼容 OpenAI 协议；不支持 stream_options 时由调用方降级
        stream_usage=LLM_STREAM_USAGE,
    )


def get_siliconflow_balance(credentials: ProviderCredentials | None = None) -> dict:
    """查询硅基流动账户余额（/v1/user/info）

    返回:
        {"ok": True, "provider": "siliconflow", "balance": "10.00", "currency": "CNY", ...}
        或
        {"ok": False, "error": "..."}
    """
    api_key = (credentials.api_key if credentials else None) or SILICONFLOW_API_KEY
    base_url = (credentials.base_url if credentials else None) or SILICONFLOW_API_BASE

    if not api_key:
        return {"ok": False, "error": "SILICONFLOW_API_KEY 未配置"}

    try:
        import requests
        resp = requests.get(
            f"{base_url.rstrip('/')}/user/info",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"SiliconFlow API 返回 {resp.status_code}: {resp.text[:200]}",
            }

        body = resp.json()
        data = body.get("data") or {}
        # SiliconFlow /user/info 返回 {"data": {"balance": "10.00", "status": "normal", ...}}
        balance = data.get("balance")
        if balance is None:
            return {"ok": False, "error": "SiliconFlow 返回中无 balance 字段", "raw": body}

        result = {
            "ok": True,
            "provider": "siliconflow",
            "balance": str(balance),
            "currency": "CNY",
            "raw": body,
        }
        if data.get("status") == "not_available":
            result["ok"] = False
            result["error"] = "SiliconFlow 账户不可用"
        return result

    except Exception as e:
        logger.error(f"[SiliconFlow] 余额查询失败: {e}")
        return {"ok": False, "error": f"请求失败: {e}"}
