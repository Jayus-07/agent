# -*- coding: utf-8 -*-
"""non_cs_detector.py — redirect_main 阶段二：LLM 语义层 non_cs 检测（2026-09-18）。

域锁（客服窗口）下，阶段一正则只转出"明显非客服"（旅游/选品强信号）。
正则未命中的问法交给本检测器做语义仲裁：LLM 判断该 query 是否"明显
不属于客服域"（闲聊、技术问答、其他业务域等），confidence ≥ 阈值才
转出主路由。

设计约束（对齐既有先例）：
- prompt 模块内硬编码（同 supervisor._llm_decision 先例，不注册 PromptSpec）
- 配置用模块内 os.getenv（同 llm_usage_store._cfg_enabled 先例，
  避开 config/customer_service.py 的多会话占用，测试直接 patch env）
- 软失败：LLM 超时/解析失败/开关关闭/短句 → 返回 None，调用方留守 CS
- _get_llm() 间接层供测试 monkeypatch
- 同 query 结果 TTL 缓存（路由层仲裁在每条消息上都可能触发，不能重复付费）
"""
import json
import logging
import os
import time
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── 配置（env 直读，默认 OFF 灰度）────────────────────────────────
ENV_LLM_ENABLED = "CS_REDIRECT_MAIN_LLM_ENABLED"
ENV_THRESHOLD = "CS_NON_CS_REDIRECT_THRESHOLD"

# 短于该长度（去空白后）不值得一次 LLM 调用："你好"/"谢谢"留守 CS
_MIN_QUERY_LEN = 6

# 路由层仲裁必须快；超时即放弃转出（留守 CS 是安全侧）
_TIMEOUT_S = 3.0

# TTL 缓存
_CACHE_TTL_S = 300.0
_CACHE_MAX = 128
_CACHE: dict[str, tuple[float, "NonCSDetection"]] = {}


def _cfg_enabled() -> bool:
    return os.getenv(ENV_LLM_ENABLED, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _threshold() -> float:
    try:
        v = float(os.getenv(ENV_THRESHOLD, "0.75"))
        return v if 0.0 <= v <= 1.0 else 0.75
    except (TypeError, ValueError):
        return 0.75


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
    """间接层：测试 monkeypatch 此函数注入 fake LLM。"""
    from backend.infra.llm import get_llm
    return get_llm()


_PROMPT_TEMPLATE = (
    "你是电商平台的智能路由仲裁器。用户当前在客服窗口，系统怀疑这条消息"
    "可能不是客服咨询。请判断该消息是否\u201c明显不属于客服域\u201d。\n\n"
    "判为非客服（is_non_cs=true）：消息明显是其他业务域的请求，如旅游规划"
    "（行程/景点/攻略）、商品选品对比、纯闲聊寒暄、技术编程问答等，且不含"
    "任何客服要素。\n"
    "判为客服（is_non_cs=false）：涉及订单/退款/发票/物流/售后/账户/商品"
    "咨询，或语义模糊、拿不准、混合信号。宁可误留守，不可误转出。\n\n"
    "用户消息: {query}\n\n"
    "只回复 JSON，不要解释："
    '{{"is_non_cs": true或false, "confidence": 0.0到1.0的小数, '
    '"target_domain": "travel或selection或chitchat或tech或other或空字符串", '
    '"reason": "一句话理由"}}'
)


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
        prompt = _PROMPT_TEMPLATE.format(query=q[:300])
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
