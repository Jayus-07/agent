"""
deepseek.py — DeepSeek Provider（云端，兼容 OpenAI 协议）

提供:
  - build_deepseek(): 构建 ChatOpenAI 实例（用 DeepSeek API base）
  - get_deepseek_balance(): 调 DeepSeek 官方余额查询 API
"""

from backend.config import (
    LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT,
    DEEPSEEK_API_KEY, DEEPSEEK_API_BASE,
)
from backend.shared.logger import logger


def build_deepseek(model_name: str) -> object:
    """构建 DeepSeek 模型实例（通过 OpenAI 兼容协议）"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ImportError(
            "deepseek provider 需要 langchain_openai 包，请 pip install langchain-openai"
        ) from e

    return ChatOpenAI(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_API_BASE,
    )


def get_deepseek_balance() -> dict:
    """调 DeepSeek 官方余额查询 API

    返回:
        {"ok": True, "provider": "deepseek", "balance": "3.99", "currency": "CNY", ...}
        或
        {"ok": False, "error": "..."}
    """
    if not DEEPSEEK_API_KEY:
        return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置"}

    try:
        import requests
        resp = requests.get(
            f"{DEEPSEEK_API_BASE.rstrip('/')}/user/balance",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"DeepSeek API 返回 {resp.status_code}: {resp.text[:200]}",
            }

        body = resp.json()
        # DeepSeek 官方返回结构:
        #   {"is_available": true,
        #    "balance_infos": [{"currency": "CNY", "total_balance": "3.99", ...}]}
        if not body.get("is_available", True):
            return {"ok": False, "error": "DeepSeek 账户余额不足", "raw": body}

        balance_infos = body.get("balance_infos") or []
        if balance_infos:
            first = balance_infos[0]
            return {
                "ok": True,
                "provider": "deepseek",
                "balance": first.get("total_balance", "0.00"),
                "currency": first.get("currency", "CNY"),
                "raw": body,
            }
        else:
            return {
                "ok": True,
                "provider": "deepseek",
                "balance": body.get("balance_available", "0.00"),
                "currency": "CNY",
                "raw": body,
            }

    except Exception as e:
        logger.error(f"[DeepSeek] 余额查询失败: {e}")
        return {"ok": False, "error": f"请求失败: {e}"}
