"""context_budget.token_counter — 模型感知 Token 计数注册表（P0-1）

tiktoken(o200k_base) 只是 OpenAI 系的兼容计数器；DeepSeek/Qwen 等
OpenAI-compatible 模型 tokenizer 不同，直接套用会低估中文 token
（实测 DeepSeek 中文约 0.6~0.7 token/字，o200k 约 0.4~0.5）。

选择优先级（实施规格 §三）：
  1. native    模型原生 tokenizer（本地可得时；经 model entry
               ``tokenizer_id`` 声明 + 可选依赖，默认不可用）
  2. compatible provider 官方兼容 tokenizer（openai 系 → tiktoken o200k）
  3. calibrated 供应商标定估算（CJK 感知字符统计 × 安全系数，estimated=True）
  4. fallback  通用 2 字符/token 粗估

约束：
  - 业务层禁止直接 import tiktoken；统一经本模块 / memory.token_budget。
  - 计数默认取「当前请求模型」（proxy 请求覆盖 > 全局 LLM_MODEL），
    显式传 ``model=`` 时以参数为准。
  - estimated 计数自动乘 CONTEXT_TOKEN_ESTIMATION_MARGIN（默认 1.10），
    宁可高估不可低估——低估才会击穿真实窗口。
  - 多模态：图片 part 不再计 0，按 CONTEXT_IMAGE_TOKEN_ESTIMATE 估算。
  - 每条消息加 CONTEXT_MESSAGE_OVERHEAD_TOKENS（对话模板包裹开销）。
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any

# CJK 统一表意文字区（含扩展A/兼容区）；估算用，无需穷尽
_CJK_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")

# 供应商标定参数（保守偏高）：CJK token/字 与 ASCII token/字符。
# 依据：DeepSeek/Qwen 中文 ~0.6-0.7 token/字、英文 ~0.25 token/字符，
# 取偏高值 + margin，保证估算 >= 实际的概率占优。
#
# _CALIBRATION_DEFAULT 实测依据（2026-09-23 STOP D，豆包 doubao-seed-2.0-mini
# 真实 usage 回测）：CJK ~0.66-0.70 token/字；**非 CJK（数字/字母/全角标点
# 混排）实测 0.585-1.0+ token/字符**——旧默认 0.33 对 ASCII/混合内容系统性
# 低估 36-40%（同批样本 CJK 类高估 16-26%，方向安全）。未知 provider 的
# ASCII 系数提到 0.75（×1.10 margin 后 ~0.83/字符），混合样本实测 +1.2%，
# 不再出现低估；已有 deepseek/qwen 等条目不动（无新数据不改）。
_CALIBRATION: dict[str, tuple[float, float]] = {
    # provider: (tokens_per_cjk_char, tokens_per_ascii_char)
    # ⚠️ 标定范围（2026-10-01 STOP D 复核）：下表 deepseek/qwen/siliconflow
    # 的 0.30 ASCII 系数**未做真实 usage 回测**（唯一回测样本是豆包，
    # 见 _CALIBRATION_DEFAULT 注释）；DeepSeek/Qwen 官方 tokenizer 对
    # ASCII/数字/JSON/工具 schema 的实际密度可能与 0.30 有显著偏差。
    # 口径：按模型采样标定（真实 usage.prompt_tokens vs 估算）之后才允许
    # 调整系数/安全余量/触发阈值；标定脚本产出落 data/ 与台账。
    "deepseek": (0.70, 0.30),
    "qwen": (0.70, 0.30),
    "siliconflow": (0.70, 0.30),
    "ollama": (0.75, 0.33),
}
_CALIBRATION_DEFAULT = (0.75, 0.75)


def _cfg(name: str, default: Any) -> Any:
    import backend.config as config
    return getattr(config, name, default)


def _get_provider(model_name: str) -> str:
    """模型 → provider（复用 proxy 的 provider 解析；失败返回空串）。"""
    if not model_name:
        return ""
    try:
        from backend.infra.llm.proxy import _get_provider_for
        return _get_provider_for(model_name) or ""
    except Exception:
        return ""


def _resolve_model(model: str | None) -> str:
    """显式参数 > 请求覆盖 > 全局默认。全部为空返回空串（走 fallback）。"""
    if model:
        return model
    try:
        from backend.infra.llm.proxy import get_request_model_name
        override = get_request_model_name()
        if override:
            return override
    except Exception:
        pass
    try:
        from backend.config.llm import LLM_MODEL
        return str(LLM_MODEL or "")
    except Exception:
        return ""


@dataclass(frozen=True)
class TokenCounter:
    """一个 (provider, 策略) 维度的计数器。estimated=True 时结果已含 margin。"""

    provider: str
    strategy: str          # native | compatible | calibrated | fallback
    estimated: bool
    _cjk: float = 0.0      # calibrated 参数
    _ascii: float = 0.0

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self.strategy in ("native", "compatible"):
            enc = _get_encoding()
            if enc is not None:
                return len(enc.encode(text))
            # tiktoken 加载失败：按 calibrated 兜底，不中断调用方
        if self.strategy == "fallback":
            return max(1, len(text) // 2)
        cjk = len(_CJK_RE.findall(text))
        return max(1, int((cjk * self._cjk + (len(text) - cjk) * self._ascii)
                          * _margin() ))


_encoding_lock = threading.Lock()
_encoding_cache = None


def _get_encoding():
    """tiktoken o200k_base 懒加载（openai compatible 策略用）。"""
    global _encoding_cache
    if _encoding_cache is not None:
        return _encoding_cache
    with _encoding_lock:
        if _encoding_cache is None:
            try:
                import tiktoken
                _encoding_cache = tiktoken.get_encoding("o200k_base")
            except Exception:
                _encoding_cache = False  # 负缓存
    return _encoding_cache or None


def _margin() -> float:
    try:
        return max(1.0, float(_cfg("CONTEXT_TOKEN_ESTIMATION_MARGIN", 1.10)))
    except Exception:
        return 1.10


_counter_cache: dict[str, TokenCounter] = {}
_counter_lock = threading.Lock()


def get_counter(model: str | None = None) -> TokenCounter:
    """按模型取计数器（进程内缓存）。选择链见模块 docstring。"""
    model_name = _resolve_model(model)
    provider = _get_provider(model_name)

    # 1. native：model entry 声明 tokenizer_id 才尝试（本地不可得时自动降级）
    entry = _get_model_entry(model_name)
    tokenizer_id = (entry or {}).get("tokenizer_id") or ""
    if tokenizer_id:
        counter = _try_native(tokenizer_id, provider)
        if counter is not None:
            return _cached(model_name, counter)

    # 2. compatible：openai 系 → tiktoken（对 openai 模型是官方口径）
    if provider == "openai" and _get_encoding() is not None:
        return _cached(model_name, TokenCounter(
            provider=provider or "unknown", strategy="compatible",
            estimated=False))

    # 3. calibrated：已知供应商标定参数；unknown 模型同样给标定估算
    if model_name:
        cjk, ascii_f = _CALIBRATION.get(provider, _CALIBRATION_DEFAULT)
        return _cached(model_name, TokenCounter(
            provider=provider or "unknown", strategy="calibrated",
            estimated=True, _cjk=cjk, _ascii=ascii_f))

    # 4. fallback：无任何模型信息（脚本/测试）→ 通用粗估
    return _cached(model_name, TokenCounter(
        provider="unknown", strategy="fallback", estimated=True))


def _cached(model_name: str, counter: TokenCounter) -> TokenCounter:
    key = f"{model_name}|{counter.strategy}|{counter.provider}"
    with _counter_lock:
        if key not in _counter_cache:
            _counter_cache[key] = counter
            _record_counter_metric(counter)
        return _counter_cache[key]


def _try_native(tokenizer_id: str, provider: str) -> TokenCounter | None:
    """native tokenizer：默认环境无 HF tokenizers/模型文件，直接放弃（降级链）。"""
    return None


def _get_model_entry(model_name: str) -> dict | None:
    if not model_name:
        return None
    try:
        from backend.infra.llm.models import get_model_entry
        return get_model_entry(model_name)
    except Exception:
        return None


def _record_counter_metric(counter: TokenCounter) -> None:
    """counter 首次创建时记录 provider/estimated 观测（低基数，防高频）。"""
    try:
        from backend.observability.metrics import context_token_counter_total
        if context_token_counter_total is not None:
            context_token_counter_total.labels(
                provider=counter.provider or "unknown",
                strategy=counter.strategy,
                estimated=str(counter.estimated).lower(),
            ).inc()
    except Exception:
        pass


def resolve_model_context_window(model: str | None = None) -> int:
    """解析目标模型真实上下文窗口：min(配置窗口, 模型注册窗口)。

    模型未注册 / 未填 context_length → 返回配置窗口（行为兼容旧版）。
    """
    from backend.config.llm import LLM_CONTEXT_LENGTH

    configured = int(LLM_CONTEXT_LENGTH)
    model_name = _resolve_model(model)
    entry = _get_model_entry(model_name)
    try:
        model_window = int((entry or {}).get("context_length") or 0)
    except (TypeError, ValueError):
        model_window = 0
    if model_window <= 0:
        return configured
    return max(0, min(configured, model_window))


# ── 公共计数入口（业务层唯一允许的调用面）─────────────────────────

def count_tokens(text: str, model: str | None = None) -> int:
    return get_counter(model).count(text or "")


def count_message_tokens(msg: Any, model: str | None = None) -> int:
    """单条消息 token 数：文本内容 + 每条固定模板开销 + 多模态图片估算。"""
    counter = get_counter(model)
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        body = counter.count(content)
    elif isinstance(content, list):
        body = 0
        for part in content:
            if not isinstance(part, dict):
                body += counter.count(str(part))
                continue
            if part.get("type") == "text" or "text" in part:
                body += counter.count(str(part.get("text", "")))
            elif part.get("type") in ("image_url", "image", "input_image"):
                body += int(_cfg("CONTEXT_IMAGE_TOKEN_ESTIMATE", 1024))
            else:
                body += counter.count(json.dumps(part, ensure_ascii=False,
                                                 default=str))
    else:
        body = counter.count(str(content))
    overhead = int(_cfg("CONTEXT_MESSAGE_OVERHEAD_TOKENS", 4))
    return body + (overhead if (body or content) else 0)


def count_messages_tokens(messages: list, model: str | None = None) -> int:
    return sum(count_message_tokens(m, model) for m in (messages or []))


def _tool_to_text(tool: Any) -> str:
    """bind_tools 形态兼容：langchain Tool / pydantic / 裸 dict / OpenAI schema。"""
    for attr in ("tool_call_schema", "args_schema", "args"):
        v = getattr(tool, attr, None)
        if v is None:
            continue
        try:
            if hasattr(v, "model_json_schema"):
                v = v.model_json_schema()
            elif hasattr(v, "schema"):
                v = v.schema()
            return json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            continue
    try:
        return json.dumps(tool, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(tool)


def count_tool_schema_tokens(tools: Any, model: str | None = None) -> int:
    """工具 schema 集 token 数（function calling 挤占输入窗口的隐性大头）。"""
    if not tools:
        return 0
    if isinstance(tools, dict):
        tools = [tools]
    counter = get_counter(model)
    total = 0
    for tool in tools:
        total += counter.count(_tool_to_text(tool))
    # OpenAI tools 信封：每工具 {"type":"function","function":{...}} ~8 token
    total += 8 * len(list(tools))
    return total


def count_response_format_tokens(response_format: Any,
                                 model: str | None = None) -> int:
    """response_format / JSON schema token 数。"""
    if not response_format:
        return 0
    counter = get_counter(model)
    if hasattr(response_format, "model_json_schema"):
        try:
            response_format = response_format.model_json_schema()
        except Exception:
            pass
    if hasattr(response_format, "schema"):
        try:
            response_format = response_format.schema()
        except Exception:
            pass
    try:
        text = json.dumps(response_format, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(response_format)
    return counter.count(text)


def truncate_text_to_tokens(text: str, max_tokens: int,
                            model: str | None = None) -> str:
    """按 token 截取文本（策略感知；calibrated 策略按估算比例换算字符数）。

    tiktoken 可用时精确截取（decode 保证 ≤ max_tokens）；否则按策略
    字符/token 比估算字符数后切片——宁可截多不可截少。
    """
    if not text or max_tokens <= 0:
        return ""
    counter = get_counter(model)
    if counter.strategy in ("native", "compatible"):
        enc = _get_encoding()
        if enc is not None:
            if len(enc.encode(text)) <= max_tokens:
                return text
            return enc.decode(enc.encode(text)[:max_tokens])
    if counter.count(text) <= max_tokens:
        return text
    # calibrated/fallback：按 1/token 平均字符数切片再校验（防超）
    per_token = max(1.0, len(text) / max(1, counter.count(text)))
    cut = int(max_tokens * per_token)
    while cut > 0 and counter.count(text[:cut]) > max_tokens:
        cut = int(cut * 0.9)
    return text[:cut]
