# -*- coding: utf-8 -*-
"""non_cs_detector.py — redirect_main 阶段二：LLM 语义层 non_cs 检测（2026-09-18）。

域锁（客服窗口）下，阶段一正则只转出"明显非客服"（旅游/选品强信号）。
正则未命中的问法交给本检测器做语义仲裁：LLM 判断该 query 是否"明显
不属于客服域"（闲聊、技术问答、其他业务域等），confidence ≥ 阈值才
转出主路由。

设计约束（对齐既有先例）：
- Prompt 通过 PromptService 管理，Key 为 customer_service.redirect_main
- 配置 env 名收口 config/customer_service.py 的 getter（调用时求值，
  测试直接 patch env 即生效；2026-09-21 审查遗留项 3.3 收敛）
- 软失败：LLM 超时/解析失败/开关关闭/短句 → 返回 None，调用方留守 CS
- _get_llm() 间接层供测试 monkeypatch
- 同 query 结果 TTL 缓存（路由层仲裁在每条消息上都可能触发，不能重复付费）
"""
import json
import logging
import time
from typing import Any, Optional

from pydantic import BaseModel
from backend.config.customer_service import (
    cs_non_cs_redirect_threshold,
    cs_redirect_main_llm_enabled,
)
from backend.customer_service.prompting import render_prompt

logger = logging.getLogger(__name__)

# 短于该长度（去空白后）不值得一次 LLM 调用："你好"/"谢谢"留守 CS
_MIN_QUERY_LEN = 6

# 路由层仲裁必须快；超时即放弃转出（留守 CS 是安全侧）
_TIMEOUT_S = 3.0

# TTL 缓存
_CACHE_TTL_S = 300.0
_CACHE_MAX = 128
_CACHE: dict[str, tuple[float, "NonCSDetection"]] = {}


def _cfg_enabled() -> bool:
    """薄委托：env 名与解析收口 config/customer_service.py（保留本名供测试 patch）。"""
    return cs_redirect_main_llm_enabled()


def _threshold() -> float:
    """薄委托：同上，保留本名供测试 patch。"""
    return cs_non_cs_redirect_threshold()


class NonCSDetection(BaseModel):
    """LLM 仲裁结果。is_non_cs=true 表示"明显不属于客服域"。"""

    is_non_cs: bool = False
    confidence: float = 0.0
    target_domain: str = ""  # travel / selection / chitchat / tech / other / ""
    reason: str = ""


def should_redirect(det: NonCSDetection) -> bool:
    """转出判定：LLM 判非客服 且 置信度达阈值。阈值在调用时读，便于灰度调整。"""
    return bool(det.is_non_cs) and det.confidence >= _threshold()


def _get_llm():
    """间接层：测试 monkeypatch 此函数注入 fake LLM。

    G7（2026-10-06 收编）：返回 llm 代理（限流/韧性/llm_usage 记账），
    不再走 get_llm() 裸实例——裸实例绕过全部包装。
    """
    from backend.infra.llm import llm
    return llm


def _parse_response(response: Any) -> Optional[NonCSDetection]:
    """解析 LLM 回复为 NonCSDetection；任何解析失败返回 None（软失败）。"""
    try:
        text = response.content if hasattr(response, "content") else str(response)
        text = (text or "").strip()
        # 容忍 ```json ... ``` 包裹
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        data = json.loads(text[start:end + 1])
        det = NonCSDetection(
            is_non_cs=bool(data.get("is_non_cs", False)),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            target_domain=str(data.get("target_domain", "") or "").strip(),
            reason=str(data.get("reason", "") or "").strip(),
        )
        det.confidence = max(0.0, min(1.0, det.confidence))
        return det
    except Exception as e:
        logger.debug(f"[NonCSDetector] 回复解析失败: {e}")
        return None


def detect_non_cs(query: str) -> Optional[NonCSDetection]:
    """单次 LLM 仲裁。开关关闭/短句/超时/解析失败一律返回 None（留守 CS）。"""
    if not _cfg_enabled():
        return None
    q = (query or "").strip()
    if len(q) < _MIN_QUERY_LEN:
        return None
    try:
        from langchain_core.messages import HumanMessage

        from backend.infra.async_utils import sync_call_with_timeout

        llm = _get_llm()
        prompt = render_prompt(
            "customer_service.redirect_main",
            query=q[:300],
        )
        response = sync_call_with_timeout(
            llm.invoke, _TIMEOUT_S, [HumanMessage(content=prompt)],
        )
        return _parse_response(response)
    except Exception as e:
        logger.debug(f"[NonCSDetector] LLM 仲裁失败，留守 CS: {e}")
        return None


def detect_non_cs_cached(query: str) -> Optional[NonCSDetection]:
    """带 TTL 缓存的仲裁入口（router_node 调这个）。None 不缓存（可恢复）。"""
    if not _cfg_enabled():
        return None
    q = (query or "").strip()
    if len(q) < _MIN_QUERY_LEN:
        return None
    now = time.monotonic()
    hit = _CACHE.get(q)
    if hit is not None and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    det = detect_non_cs(q)
    if det is not None:
        if len(_CACHE) >= _CACHE_MAX:
            # FIFO 淘汰最旧一条，防无界膨胀
            oldest = next(iter(_CACHE))
            _CACHE.pop(oldest, None)
        _CACHE[q] = (now, det)
    return det
