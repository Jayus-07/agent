"""
vllm.py — vLLM 自托管 Provider（OpenAI 兼容协议）

提供:
  - build_vllm(): 构建 ChatOpenAI 实例（指向自托管 vLLM 服务）
  - get_vllm_balance(): 自托管无计费，探活 /v1/models 返回服务状态

服务器侧部署见 workspace/vllm-server-deploy/（deploy.sh 一键部署）。
.env 需配置:
  VLLM_API_BASE=http://localhost:8000/v1   # 经 SSH 隧道或直连
  VLLM_API_KEY=<deploy.sh 生成的 Key>
"""

from backend.config import (
    LLM_CONTEXT_LENGTH,
    LLM_REQUEST_TIMEOUT,
    LLM_STREAM_USAGE,
    LLM_TEMPERATURE,
    VLLM_API_BASE,
    VLLM_API_KEY,
)
from backend.shared.logger import logger


def build_vllm(model_name: str) -> object:
    """构建 vLLM 模型实例（OpenAI 兼容协议，model_name 须与服务器 --model 一致）"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ImportError(
            "vllm provider 需要 langchain_openai 包，请 pip install langchain-openai"
        ) from e

    # vLLM 对 max_tokens 超出模型上下文的请求会直接 400，
    # 而 ChatOpenAI 默认 max_tokens=None 时不传该字段，这里显式透传全局配置
    return ChatOpenAI(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
        api_key=VLLM_API_KEY or "EMPTY",  # 未设 Key 的自托管实例用占位符
        base_url=VLLM_API_BASE,
        stream_usage=LLM_STREAM_USAGE,
    )


def get_vllm_balance() -> dict:
    """自托管服务无计费概念；用 GET /v1/models 探活代替余额查询。

    返回结构与 deepseek 等一致：{"ok": ..., "provider": "vllm", ...}
    """
    base = VLLM_API_BASE.rstrip("/")
    try:
        import requests
        resp = requests.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {VLLM_API_KEY}"} if VLLM_API_KEY else {},
            timeout=5,
        )
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"vLLM 服务返回 {resp.status_code}: {resp.text[:200]}",
            }
        served = [m.get("id") for m in resp.json().get("data", [])]
        return {
            "ok": True,
            "provider": "vllm",
            "balance": "self-hosted",  # 无计费，占位展示
            "currency": "-",
            "served_models": served,
        }
    except Exception as e:
        logger.error(f"[vLLM] 服务探活失败: {e}")
        return {"ok": False, "error": f"vLLM 服务不可达: {e}"}
