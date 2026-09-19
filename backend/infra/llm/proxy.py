"""
proxy.py — _LLMProxy 代理对象 + 模块级 llm 单例

核心设计:
  - 懒加载：首次调用时才初始化 LLM，根据 LLM_MODEL 自动选择 Provider
  - `llm` 是 _LLMProxy 代理；每次 .invoke()/.stream() 都委派给当前活跃模型
  - LLMFactory.set_current("deepseek-chat") 后，所有 `llm.invoke(...)` 自动走新模型
  - 所有调用方 `from backend.infra.llm import llm` 无需修改
  - 每次调用记录 token + finish_reason + cost_usd 到模块级 _last_call_meta（供 tracer 读取）
  - P1-7: 韧性链 — 瞬时错误显式重试（指数退避）→ 熔断开路/重试耗尽时
    切备用模型（LLM_FALLBACK_MODEL）→ 最终降级为固定话术
"""
from __future__ import annotations

import asyncio
import contextvars as _contextvars
import inspect
import threading
import time

from typing import TYPE_CHECKING

# BaseChatModel 仅作类型标注（TYPE_CHECKING 化）—— langchain_core 1.4.x 的
# chat_models 在装了 transformers 的环境下连带导入 torch（实测 ~8s）。
if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

from backend.config import LLM_MODEL
from backend.config.llm import (
    LLM_ALLOW_DEGRADED_ANSWER,
    LLM_FALLBACK_MODEL,
    LLM_MAX_RETRIES,
    LLM_RETRY_BACKOFF_BASE,
    OLLAMA_ENABLED,
)
from backend.infra.llm.factory import get_llm_factory
from backend.infra.llm.models import AVAILABLE_MODELS, compute_cost_usd
from backend.shared.logger import logger

# =====================================================
# 懒加载默认 LLM
# =====================================================

_default_llm = None
_default_lock = threading.Lock()

# PR-0.4: 当前请求的 user_id（限流用）— 调用方可通过 set_current_user_id() 设置。
# 用 ContextVar 而非全局变量：全局变量在并发请求下互相覆盖，
# 会把限流扣减算到错误的用户头上
_user_id_var: _contextvars.ContextVar[str | None] = _contextvars.ContextVar(
    "llm_current_user_id", default=None,
)


def set_current_user_id(user_id: str | None) -> None:
    """设置当前请求上下文的 user_id（限流用）。FastAPI 路由层在每次请求开始时调用。"""
    _user_id_var.set(user_id)


def _thread_local_user_id() -> str | None:
    """读取当前请求的 user_id（限流用）。"""
    return _user_id_var.get()


# 按请求模型覆盖：RequestContext.model → bind() 时设置（空 = 用全局模型）。
# ContextVar 隔离并发请求，与 user_id 限流变量同一模式。
_request_model_var: _contextvars.ContextVar[str] = _contextvars.ContextVar(
    "llm_request_model", default="",
)


def set_request_model(model: str) -> None:
    """设置当前请求的模型覆盖。空串清除；非法模型名忽略（回退全局 LLM_MODEL）。

    校验口径与 LLMFactory.set_current 对齐（2026-09-16）：
    除注册表外，还校验 provider API Key 与 cloud 模式 Ollama 禁用——
    否则配置缺失要拖到首次 invoke 才暴露（401/构建异常）。
    """
    model = (model or "").strip()
    if not model:
        _request_model_var.set("")
        return
    import os

    from backend.infra.llm.models import AVAILABLE_MODELS, PROVIDER_API_KEY_ENV
    if model not in {m["name"] for m in AVAILABLE_MODELS}:
        logger.warning(f"[LLM:proxy] 忽略非法模型覆盖: {model} "
                       f"(可用: {[m['name'] for m in AVAILABLE_MODELS]})")
        _request_model_var.set("")
        return
    provider = _get_provider_for(model)
    if provider == "ollama" and not OLLAMA_ENABLED:
        logger.warning(
            f"[LLM:proxy] 忽略模型覆盖 {model}: Ollama 当前未启用 "
            f"(回退全局 {get_active_model_name()})"
        )
        _request_model_var.set("")
        return
    key_env = PROVIDER_API_KEY_ENV.get(provider)
    if key_env and not os.getenv(key_env, "").strip():
        logger.warning(f"[LLM:proxy] 忽略模型覆盖 {model}: {key_env} 未配置 "
                       f"(回退全局 {get_active_model_name()})")
        _request_model_var.set("")
        return
    _request_model_var.set(model)


# 覆盖模型的实例缓存（独立于全局默认模型，按需构建）
_override_llm_cache: dict[str, BaseChatModel] = {}
_override_llm_lock = threading.Lock()


def _get_override_llm(model_name: str) -> BaseChatModel:
    """获取按请求覆盖模型的实例（懒构建 + 缓存）。"""
    with _override_llm_lock:
        inst = _override_llm_cache.get(model_name)
        if inst is None:
            logger.info(f"[LLM:proxy] 按请求模型覆盖生效: {model_name}")
            inst = _build_llm_for(model_name)
            _override_llm_cache[model_name] = inst
        return inst


# =====================================================
# P1 真 token 级流式：per-turn 增量 sink
# =====================================================
# 生成节点（RAG 链 / Reporter）切到 llm.stream 后，由调用方在每个内容
# chunk 到达时显式调用 emit_stream_delta，把增量文本推给 SSE 层。
# proxy 不自动转发：RAG 链的增量要经 MetaStreamFilter 过滤机读尾部，
# 自动转发会双发并泄漏 META（2026-09-14 修复打字机失效时统一约定）。
# ContextVar 按上下文隔离：并发请求各推各的，不会串味；
# LangGraph/LCEL 在同线程执行节点（含 asyncio.run 包装），上下文可达。
# 限制：LangGraph 并行 Send 的分支任务在内部线程池执行，上下文不可达 →
# 该分支不产生 delta（最终答案仍由节点输出兜底，仅少流式体验）。

_stream_sink_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_stream_sink", default=None,
)


def set_stream_sink(sink) -> None:
    """设置当前上下文的流式增量回调 sink(text: str) -> None。"""
    _stream_sink_var.set(sink)


def reset_stream_sink() -> None:
    """清除当前上下文的流式 sink（请求结束时调用，防线程复用串味）。"""
    _stream_sink_var.set(None)


def emit_stream_delta(text: str, kind: str = "answer") -> bool:
    """把生成增量转发给当前 sink。无 sink / 空 text / sink 失败均静默跳过。

    Args:
        text: 增量文本
        kind: "answer"（默认，正文 delta）或 "thinking"（推理模型思考链增量）
    Returns:
        是否真正转发了（供调用方判断本轮是否发生过流式输出）。
    """
    if not text:
        return False
    sink = _stream_sink_var.get()
    if sink is None:
        return False
    try:
        try:
            sink(text, kind)
        except TypeError:
            # 兼容旧式单参 sink(text)（测试桩/未升级的调用方）
            sink(text)
        return True
    except Exception:
        return False


def extract_chunk_text(chunk) -> str:
    """从流式 chunk 提取文本（兼容 str / AIMessageChunk / 多模态 content list）。"""
    if isinstance(chunk, str):
        return chunk
    c = getattr(chunk, "content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(
            p.get("text", "") for p in c if isinstance(p, dict)
        )
    return str(c) if c else ""


def extract_chunk_reasoning(chunk) -> str:
    """从流式 chunk 提取思考链增量（推理模型 reasoning_content）。

    LangChain 不解析 reasoning_content，思考型模型（deepseek-reasoner /
    qwen3 thinking）流式期间它挂在 additional_kwargs 上且 content 为空。
    非思考模型/str chunk 返回空串，调用方零成本跳过。
    """
    if isinstance(chunk, str):
        return ""
    kw = getattr(chunk, "additional_kwargs", None) or {}
    reasoning = kw.get("reasoning_content", "")
    return reasoning if isinstance(reasoning, str) else ""


def _get_provider_for(model_name: str) -> str:
    """根据模型名查找所属 provider"""
    for m in AVAILABLE_MODELS:
        if m["name"] == model_name:
            return m["provider"]
    return "ollama"  # 兜底


def _build_llm_for(model_name: str) -> BaseChatModel:
    """按模型名构建 Provider 实例（不缓存 — 缓存由调用方管理）。"""
    provider = _get_provider_for(model_name)
    if provider == "deepseek":
        from backend.infra.llm.providers.deepseek import build_deepseek
        return build_deepseek(model_name)
    if provider == "minimax":
        from backend.infra.llm.providers.minimax import build_minimax
        return build_minimax(model_name)
    if provider == "qwen":
        from backend.infra.llm.providers.qwen import build_qwen
        return build_qwen(model_name)
    if provider == "qwen_tp":
        # Token Plan 模型包端点（@tp 后缀）。此前 proxy 缺此分支，
        # @tp 模型落到 ollama 兜底被 cloud 模式拒绝（models.py/factory.py
        # 均已注册 qwen_tp，proxy 构建口径 2026-09-17 对齐）
        from backend.infra.llm.providers.qwen_tp import build_qwen_tp
        return build_qwen_tp(model_name)
    if provider == "siliconflow":
        from backend.infra.llm.providers.siliconflow import build_siliconflow
        return build_siliconflow(model_name)
    # ollama / 兜底 — 模型选择完全由 env 配置驱动（LLM_MODEL / 请求覆盖），
    # 构建层不再按 ENV_MODE 拒建（2026-09-17 拍板：不做 cloud/local 区分）。
    # 用户配了本地模型但 Ollama 未运行时，invoke 阶段自然报连接错误。
    from langchain_ollama import ChatOllama

    from backend.config import LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT, LLM_TEMPERATURE
    return ChatOllama(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        num_ctx=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
    )


# P1-7: 备用模型实例缓存（独立于主模型，失败不相互污染）
_fallback_llm = None
_fallback_lock = threading.Lock()


def _get_fallback_llm() -> BaseChatModel | None:
    """获取备用模型实例（未配置 LLM_FALLBACK_MODEL 时返回 None）。"""
    global _fallback_llm
    if not LLM_FALLBACK_MODEL:
        return None
    if _fallback_llm is not None:
        return _fallback_llm
    with _fallback_lock:
        if _fallback_llm is None:
            try:
                logger.info(f"[LLM:resilience] 初始化备用模型: {LLM_FALLBACK_MODEL}")
                _fallback_llm = _build_llm_for(LLM_FALLBACK_MODEL)
            except Exception as e:
                logger.warning(f"[LLM:resilience] 备用模型初始化失败: {e}")
                return None
    return _fallback_llm


# =====================================================
# P1-7: 韧性链（重试 + 熔断 fallback + 降级话术）
# =====================================================

# 瞬时错误特征（异常类型名子串匹配 — LangChain 各 Provider 包装后的
# 异常类名不统一，子串匹配是务实做法）
_TRANSIENT_MARKERS = (
    "timeout", "timedout", "connection", "connect", "ratelimit",
    "serviceunavailable", "overloaded", "temporary", "badgateway",
    "apitimeout", "network",
)

_DEGRADED_ANSWER = (
    "抱歉，AI 服务当前暂时不可用（上游模型故障），请稍后重试。"
    "你稍后也可以在「模型管理」页切换到其他可用模型。"
)


def _is_transient(err: BaseException) -> bool:
    name = type(err).__name__.lower()
    return any(m in name for m in _TRANSIENT_MARKERS)


def _degraded_answer(reason: str = ""):
    """构造降级 AIMessage（结构与正常 LLM 返回一致）。

    2026-09-15：**必须带可识别标记**。此消息会流向下游结构化消费者
    （JSON 解析 / SQL 解析 / 校验器），若不标记就会被当成真实模型内容
    解析，产出"检测到 Alias"这类与真实原因（LLM 401/超时）毫无关系的
    报错。消费者可用 is_degraded_response() 判定后 fail-fast。
    """
    from langchain_core.messages import AIMessage
    return AIMessage(
        content=_DEGRADED_ANSWER,
        additional_kwargs={
            "llm_degraded": True,
            "llm_degrade_reason": reason[:200],
        },
        response_metadata={"llm_degraded": True, "llm_degrade_reason": reason[:200]},
    )


def is_degraded_response(msg) -> bool:
    """判断 LLM 返回是否为降级话术（结构化消费者应据此 fail-fast）。

    双通道判定：additional_kwargs/response_metadata 标记为主（LLM_ALLOW_DEGRADED_ANSWER
    开启时由 _degraded_answer 打上）；正文前缀匹配为辅——兼容标记丢失
    （部分 provider 重打包消息）以及跨进程/持久化后仅剩文本的场景。
    """
    if msg is None:
        return False
    for attr in ("additional_kwargs", "response_metadata"):
        meta = getattr(msg, attr, None)
        if isinstance(meta, dict) and meta.get("llm_degraded"):
            return True
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content.strip().startswith(_DEGRADED_ANSWER[:24])
    return False


def _notify_degradation(code: str, detail: dict) -> None:
    """降级事件告警（best-effort，失败不影响主流程）。"""
    try:
        from backend.observability.alerts import log_degradation, make_alert
        log_degradation(make_alert(code, detail))
    except Exception:
        logger.debug("降级告警发送失败", exc_info=True)


def _handle_terminal_failure(err: BaseException, args, kwargs):
    """重试耗尽/熔断开路后的统一兜底：备用模型 → 降级话术 → 抛原异常。"""
    reason = f"{type(err).__name__}: {str(err)[:120]}"
    # 1) 备用模型
    fb = _get_fallback_llm()
    if fb is not None:
        from backend.infra.llm.budget import reserve_model_call

        # 预算预占必须发生在 fallback 真正执行前；超限不能被下面的
        # “备用模型失败”兜底逻辑吞掉，否则会绕过硬阻断。
        reserve_model_call("fallback")
        try:
            result = fb.invoke(*args, **kwargs)
            logger.info(f"[LLM:resilience] 备用模型接管成功 ({reason})")
            _notify_degradation("LLM_FALLBACK_USED", {"reason": reason, "model": LLM_FALLBACK_MODEL})
            return result
        except Exception as e:
            logger.warning(f"[LLM:resilience] 备用模型也失败: {e}")
    # 2) 降级话术（可关 — 某些调用方需要真实异常驱动自己的降级逻辑）
    if LLM_ALLOW_DEGRADED_ANSWER:
        logger.warning(f"[LLM:resilience] 最终降级为拒答话术 ({reason})")
        _notify_degradation("LLM_DEGRADED_ANSWER", {"reason": reason})
        return _degraded_answer(reason)
    # 3) 抛回原异常（默认路径 — fail-fast，见 config/llm.py 注释）
    raise err


async def _ahandle_terminal_failure(err: BaseException, args, kwargs):
    """async 版兜底：备用模型 → 降级话术 → 抛原异常。"""
    reason = f"{type(err).__name__}: {str(err)[:120]}"
    fb = _get_fallback_llm()
    if fb is not None:
        from backend.infra.llm.budget import reserve_model_call

        reserve_model_call("fallback")
        try:
            result = await fb.ainvoke(*args, **kwargs)
            logger.info(f"[LLM:resilience] 备用模型接管成功 ({reason})")
            _notify_degradation("LLM_FALLBACK_USED", {"reason": reason, "model": LLM_FALLBACK_MODEL})
            return result
        except Exception as e:
            logger.warning(f"[LLM:resilience] 备用模型也失败: {e}")
    if LLM_ALLOW_DEGRADED_ANSWER:
        logger.warning(f"[LLM:resilience] 最终降级为拒答话术 ({reason})")
        _notify_degradation("LLM_DEGRADED_ANSWER", {"reason": reason})
        return _degraded_answer(reason)
    raise err


def _call_with_resilience(attr, *args, **kwargs):
    """同步韧性调用：重试 → 熔断/重试耗尽 → fallback。"""
    from backend.infra.circuit_breaker import CircuitBreakerOpenError, llm_circuit_breaker
    from backend.infra.llm.budget import reserve_model_call

    last_err: BaseException | None = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        reserve_model_call("primary" if attempt == 0 else "retry")
        try:
            return llm_circuit_breaker.call(attr, *args, **kwargs)
        except CircuitBreakerOpenError as e:
            # 熔断开路：立即兜底（快速失败是熔断的目的，不做无意义等待）
            logger.warning(f"[LLM:resilience] 熔断开路: {e}")
            _notify_degradation("LLM_CIRCUIT_OPEN", {"retry_in": round(e.retry_in, 1)})
            return _handle_terminal_failure(e, args, kwargs)
        except Exception as e:
            last_err = e
            if not _is_transient(e):
                break  # 非瞬时错误（鉴权/参数等）重试无意义
            if attempt >= LLM_MAX_RETRIES:
                break
            delay = LLM_RETRY_BACKOFF_BASE ** (attempt + 1)
            logger.warning(
                f"[LLM:resilience] 瞬时错误重试 {attempt + 1}/{LLM_MAX_RETRIES} "
                f"({type(e).__name__}, {delay:.1f}s 后重试)"
            )
            time.sleep(delay)
    return _handle_terminal_failure(last_err, args, kwargs)


async def _acall_with_resilience(attr, *args, **kwargs):
    """异步韧性调用（对称于 _call_with_resilience）。"""
    from backend.infra.circuit_breaker import CircuitBreakerOpenError, llm_circuit_breaker
    from backend.infra.llm.budget import reserve_model_call

    last_err: BaseException | None = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        reserve_model_call("primary" if attempt == 0 else "retry")
        try:
            return await llm_circuit_breaker.acall(attr, *args, **kwargs)
        except CircuitBreakerOpenError as e:
            logger.warning(f"[LLM:resilience] 熔断开路: {e}")
            _notify_degradation("LLM_CIRCUIT_OPEN", {"retry_in": round(e.retry_in, 1)})
            return await _ahandle_terminal_failure(e, args, kwargs)
        except Exception as e:
            last_err = e
            if not _is_transient(e):
                break
            if attempt >= LLM_MAX_RETRIES:
                break
            delay = LLM_RETRY_BACKOFF_BASE ** (attempt + 1)
            logger.warning(
                f"[LLM:resilience] 瞬时错误重试 {attempt + 1}/{LLM_MAX_RETRIES} "
                f"({type(e).__name__}, {delay:.1f}s 后重试)"
            )
            await asyncio.sleep(delay)
    return await _ahandle_terminal_failure(last_err, args, kwargs)


def _build_default_llm():
    """根据 LLM_MODEL 构建正确的 Provider 实例"""
    global _default_llm
    if _default_llm is not None:
        return _default_llm

    with _default_lock:
        if _default_llm is not None:
            return _default_llm

        logger.info(f"正在初始化默认 LLM: {LLM_MODEL}")
        _default_llm = _build_llm_for(LLM_MODEL)
        logger.info(f"LLM init OK: {LLM_MODEL} (provider={_get_provider_for(LLM_MODEL)})")
        return _default_llm


def _resolve_active_llm() -> BaseChatModel:
    """返回当前生效的 LLM 实例：请求覆盖 > factory 缓存 > 懒加载默认。

    每次调用都实时获取工厂实例，确保模型切换后立即生效。
    """
    override = _request_model_var.get()
    if override:
        factory = get_llm_factory()
        if factory is not None:
            cached = factory._instance_cache.get(override)
            if cached is not None:
                return cached
        return _get_override_llm(override)
    factory = get_llm_factory()
    if factory is not None:
        cached = factory._instance_cache.get(factory._current_model)
        if cached is not None:
            return cached
    return _build_default_llm()


def get_active_model_name() -> str:
    """返回当前生效模型名（解析顺序与 _resolve_active_llm 一致）。

    供日志/trace 标注"这次 LLM 调用用的哪个模型"
    （请求覆盖 > factory 当前 > 全局默认 LLM_MODEL）。
    """
    override = _request_model_var.get()
    if override:
        return override
    factory = get_llm_factory()
    if factory is not None:
        return factory._current_model
    return LLM_MODEL


# =====================================================
# <think> 剥离（防御性，正规方案是 Provider 传 reasoning_split）
# =====================================================

def _strip_think(text: str) -> str:
    """剥离模型输出的 <think>...</think> 推理块"""
    import re
    text = re.sub(r'<think>[\s\S]*?</think>\s*', '', text)
    text = re.sub(r'<think>[\s\S]*', '', text)
    return text.strip()


# =====================================================
# 最近一次 LLM 调用元数据（token / finish_reason / cost）
# =====================================================
# P1 并发隔离：模块级 dict 会被并发请求互相覆盖（A 的 token 被 B 覆盖，
# tracer 读到 B 的）。改用 ContextVar 按调用上下文隔离；
# 读写必须整体 set()/get()（不可变替换），禁止对 .get() 返回的 dict
# 做 clear()/update()（会改到共享对象）。

_last_tokens_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_last_tokens", default={},
)
_last_call_meta_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_last_call_meta", default={},
)

# P0 Token 看板：per-turn 用量累加器（按调用上下文隔离）。
# 一轮对话会产生多次 LLM 调用（planner/worker/reporter...），单次
# _last_tokens_var 会被覆盖，这里按模型累加整轮用量，trace finish 时兜底读取。
_turn_usage_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_turn_usage", default=None,
)


def _accumulate_turn_usage(model: str, p: int, c: int, t: int,
                           cached: int, reasoning: int, cost: float) -> None:
    """把单次 LLM 调用用量累加到当前上下文的 per-turn 汇总。"""
    if not model:
        model = LLM_MODEL
    acc = dict(_turn_usage_var.get() or {})
    entry = dict(acc.get(model) or {
        "provider": _get_provider_for(model),
        "calls": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "cached_tokens": 0, "reasoning_tokens": 0, "cost_usd": 0.0,
    })
    entry["calls"] = int(entry["calls"]) + 1
    entry["prompt_tokens"] = int(entry["prompt_tokens"]) + int(p or 0)
    entry["completion_tokens"] = int(entry["completion_tokens"]) + int(c or 0)
    entry["total_tokens"] = int(entry["total_tokens"]) + int(t or 0)
    entry["cached_tokens"] = int(entry["cached_tokens"]) + int(cached or 0)
    entry["reasoning_tokens"] = int(entry["reasoning_tokens"]) + int(reasoning or 0)
    entry["cost_usd"] = float(entry["cost_usd"]) + float(cost or 0.0)
    acc[model] = entry
    _turn_usage_var.set(acc)


def get_turn_usage() -> dict:
    """读取当前上下文本轮 LLM 用量汇总：{model: {calls, tokens..., cost_usd}}。"""
    return dict(_turn_usage_var.get() or {})


def reset_turn_usage() -> None:
    """清空本轮用量（trace start/finish 时调用，防线程池复用线程串味）。"""
    _turn_usage_var.set(None)


def _usage_component() -> str:
    """usage 明细行的 component 归因（2026-09-18 窗口级 token 核算）。

    客服域轮次（cs_prefilter 命中时已在 trace.tags 打 cs_target）→
    "customer_service"；其余主问答/工具链路沿用 PG 列默认 "llm"。
    token 看板 list_calls/dashboard 已支持按 component 过滤，无需改 store。
    软失败：trace 不可达时回退默认值，不影响主链路。
    """
    try:
        from backend.observability.tracer import trace_collector
        t = trace_collector.current()
        if t is not None and t.tags.get("cs_target"):
            return "customer_service"
    except Exception:
        pass
    return "llm"


def _record_tokens(result, duration_ms: float | None = None):
    """从 LLM 返回值提取 token + finish_reason + cost，存为 dict 供 tracer 读取。

    无 token_usage 时清空 _last_tokens_var 和 _last_call_meta_var。
    duration_ms: 调用方（wrapper）计得的本次 LLM 调用耗时（含重试/熔断等待）。
    """
    try:
        tu = {}
        if hasattr(result, "response_metadata") and result.response_metadata:
            tu = result.response_metadata.get("token_usage", {})
        if not tu and hasattr(result, "usage_metadata") and result.usage_metadata:
            tu = result.usage_metadata
        # ChatAnthropic 用 input_tokens/output_tokens，ChatOpenAI 用 prompt_tokens/completion_tokens
        p = tu.get("prompt_tokens", tu.get("input_tokens", 0))
        c = tu.get("completion_tokens", tu.get("output_tokens", 0))
        t = tu.get("total_tokens", p + c)
        if not t:
            _last_tokens_var.set({})
            _last_call_meta_var.set({})
            return

        try:
            from backend.infra.llm.budget import record_model_usage

            record_model_usage(
                prompt_tokens=p,
                completion_tokens=c,
                total_tokens=t,
            )
        except Exception:
            # 预算记录是观测/门禁辅助，不能反向破坏模型主链路。
            pass

        # 细粒度用量：缓存命中 / 推理 token。
        # LangChain 统一在 usage_metadata.input_token_details（cache_read/cache_creation）
        # 与 output_token_details（reasoning）透传；上游未返回时为 0。
        # 注意 cached_tokens 是 prompt_tokens 的子集（计费口径），仅作明细展示。
        in_details = tu.get("input_token_details") or {}
        out_details = tu.get("output_token_details") or {}
        cached = int(in_details.get("cache_read", 0) or 0)
        reasoning = int(out_details.get("reasoning", 0) or 0)
        _last_tokens_var.set({
            "prompt_tokens": p, "completion_tokens": c, "total_tokens": t,
            "cached_tokens": cached, "reasoning_tokens": reasoning,
        })

        # 实际模型名（response_metadata 优先，兜底全局配置）；成本按实际模型计价
        model = ""
        if hasattr(result, "response_metadata") and result.response_metadata:
            model = (result.response_metadata.get("model_name", "")
                     or result.response_metadata.get("model", ""))

        # Prometheus 指标：LLM token 用量
        try:
            from backend.observability.metrics import llm_tokens_total
            if model and p:
                llm_tokens_total.labels(model=model, direction="prompt").inc(p)
            if model and c:
                llm_tokens_total.labels(model=model, direction="completion").inc(c)
        except Exception:
            pass  # 指标记录失败不影响核心链路

        finish_reason = "unknown"
        if hasattr(result, "response_metadata") and result.response_metadata:
            finish_reason = result.response_metadata.get(
                "finish_reason",
                result.response_metadata.get("stop_reason", "unknown"),
            )
        model = model or LLM_MODEL
        cost = compute_cost_usd(model, p, c)
        _last_call_meta_var.set({
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": t,
            "cached_tokens": cached,
            "reasoning_tokens": reasoning,
            "finish_reason": finish_reason,
            "cost_usd": cost,
            "model": model,
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else 0.0,
        })
        _accumulate_turn_usage(model, p, c, t, cached, reasoning, cost)

        # Token 看板明细落库（每调用一行，软失败不影响主链路）
        try:
            from backend.observability.llm_usage_store import (
                current_usage_attribution,
                get_llm_usage_store,
            )
            from backend.infra.llm.budget import current_call_decision
            attribution = current_usage_attribution()
            get_llm_usage_store().record({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                             + f".{int(time.time() % 1 * 1000):03d}Z",
                "trace_id": attribution["trace_id"],
                "request_id": attribution["request_id"],
                "session_id": attribution["session_id"],
                "user_id": attribution["user_id"],
                "tenant_id": attribution["tenant_id"],
                "component": _usage_component(),
                "model": model,
                "provider": _get_provider_for(model),
                "prompt_tokens": p,
                "completion_tokens": c,
                "total_tokens": t,
                "cached_tokens": cached,
                "reasoning_tokens": reasoning,
                "cost_usd": cost,
                "finish_reason": finish_reason,
                "decision": current_call_decision(),
                "duration_ms": round(duration_ms, 1) if duration_ms is not None else 0.0,
            })
        except Exception:
            pass
    except Exception:
        _last_tokens_var.set({})
        _last_call_meta_var.set({})

def _wrap_result(result):
    """递归剥离 LLM 返回值中的 <think> 块，兼容 str / AIMessage / list / dict"""
    if isinstance(result, str):
        return _strip_think(result)
    if hasattr(result, 'content') and isinstance(result.content, str):
        result.content = _strip_think(result.content)
    if isinstance(result, list):
        return [_wrap_result(r) for r in result]
    if isinstance(result, dict):
        return {k: _wrap_result(v) for k, v in result.items()}
    return result


# =====================================================
# 限流执行
# =====================================================

def _enforce_rate_limit(user_id: str | None) -> None:
    """根据 LLM_RATE_LIMIT_ENFORCE 配置执行限流。

    Phase 5: Redis 可用时使用分布式限流（跨进程一致），否则 fallback 到进程内限流。
    二者互斥，不再叠加执行（原先两套都生效 = 同一请求双重扣减）。
    off    → 仅日志（当前行为），不阻塞请求
    wait   → 阻塞等待直到获取到令牌（轮询间隔 0.1s）
    reject → 获取不到令牌时抛 RateLimitError
    """
    from backend.config.llm import LLM_RATE_LIMIT_BURST, LLM_RATE_LIMIT_ENFORCE, LLM_RATE_LIMIT_QPS

    distributed = _try_distributed_rate_limit(user_id, LLM_RATE_LIMIT_QPS, LLM_RATE_LIMIT_BURST)
    if distributed is None:
        # 分布式限流不可用（Redis 缺失/异常）→ 进程内限流兜底
        from backend.infra.llm.rate_limiter import get_rate_limiter
        passed = get_rate_limiter().acquire(user_id=user_id)
    else:
        passed = distributed

    if passed:
        return

    if LLM_RATE_LIMIT_ENFORCE == "reject":
        from backend.infra.llm.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()
        raise RateLimitError(
            f"LLM rate limit exceeded (user={user_id}), "
            f"retry after {limiter.retry_after_seconds(user_id):.1f}s"
        )

    if LLM_RATE_LIMIT_ENFORCE == "wait":
        import time

        from backend.infra.llm.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()
        wait = limiter.retry_after_seconds(user_id)
        logger.info(f"[RateLimit] wait mode: sleeping {wait:.1f}s (user={user_id})")
        time.sleep(wait)
        return

    logger.warning(f"[RateLimit] rate limited but enforce=off, proceeding (user={user_id})")


def _try_distributed_rate_limit(user_id: str | None, qps: float, burst: float) -> bool | None:
    """尝试分布式限流。

    Returns:
        True/False: 分布式限流的获取结果（True=通过）
        None: Redis 不可用，调用方需 fallback 到进程内限流
    """
    try:
        from backend.infra.redis.client import get_redis
        if get_redis() is None:
            return None
        from backend.infra.llm.distributed_rate_limiter import get_distributed_rate_limiter
        drl = get_distributed_rate_limiter()
        if not drl.acquire("global", "all", burst, qps):
            return False
        if user_id and not drl.acquire("user", user_id, max(1.0, burst / 10), max(1.0, qps / 10)):
            return False
        return True
    except Exception:
        return None


class RateLimitError(RuntimeError):
    """LLM 限流拒绝时抛出。"""


class _BoundLLMProxy:
    """bind_tools 返回的 RunnableBinding 包装。

    _LLMProxy.__getattr__ 只包装 _WRAP_METHODS 里的方法名，bind_tools
    若原样透传，拿到的是真实实例上的 RunnableBinding——其后续
    invoke/ainvoke 会绕过限流、韧性链（重试/熔断/fallback）与 token
    记录。本类把绑定后的调用重新纳入与 _LLMProxy 相同的包装路径；
    其余属性（如 bind / with_config）原样透传。
    """

    def __init__(self, bound):
        self._bound = bound

    def invoke(self, *args, **kwargs):
        user_id = kwargs.get("user_id") or _thread_local_user_id()
        _enforce_rate_limit(user_id)
        _t0 = time.monotonic()
        result = _call_with_resilience(self._bound.invoke, *args, **kwargs)
        _record_tokens(result, duration_ms=(time.monotonic() - _t0) * 1000)
        return _wrap_result(result)

    async def ainvoke(self, *args, **kwargs):
        user_id = kwargs.get("user_id") or _thread_local_user_id()
        _enforce_rate_limit(user_id)
        _t0 = time.monotonic()
        result = await _acall_with_resilience(self._bound.ainvoke, *args, **kwargs)
        _record_tokens(result, duration_ms=(time.monotonic() - _t0) * 1000)
        return _wrap_result(result)

    def bind_tools(self, *args, **kwargs):
        # 链式绑定（罕见）：继续走包装，不裸透传
        return _BoundLLMProxy(self._bound.bind_tools(*args, **kwargs))

    def __getattr__(self, name: str):
        return getattr(self._bound, name)


def bind_tools_for_model(model_name: str, tools) -> "_BoundLLMProxy | None":
    """为指定模型构建 bind_tools 包装（专用轻量模型场景，如 tool_selector）。

    返回 None（model_name 为空 / 未注册 / 构建失败）时调用方应回退
    全局 llm.bind_tools(tools)。实例经 _get_override_llm 缓存，且用
    _BoundLLMProxy 包装——专用模型同样走限流/韧性链/token 记录。
    """
    if not model_name:
        return None
    try:
        from backend.infra.llm.models import AVAILABLE_MODELS
        if model_name not in {m["name"] for m in AVAILABLE_MODELS}:
            logger.warning(
                f"[LLM:proxy] 专用模型未注册: {model_name}，回退全局模型")
            return None
        inst = _get_override_llm(model_name)
        return _BoundLLMProxy(inst.bind_tools(tools))
    except Exception as e:
        logger.warning(f"[LLM:proxy] 专用模型 bind_tools 失败，回退全局: {e}")
        return None


# =====================================================
# 代理对象
# =====================================================

class _LLMProxy:
    """代理对象：每次调用都实时委派给当前活跃 LLM，并全局剥离 <think> 块"""

    __slots__ = ()
    _WRAP_METHODS = {'invoke', 'ainvoke', 'generate', 'agenerate', 'batch', 'stream', 'astream'}

    def __getattr__(self, name: str):
        target = _resolve_active_llm()
        attr = getattr(target, name)
        # bind_tools 返回 RunnableBinding，其 invoke/ainvoke 不在
        # _WRAP_METHODS 内，裸透传会绕过限流/韧性链/token 记录——
        # 用 _BoundLLMProxy 重新纳入包装（function calling 路径）
        if name == "bind_tools" and callable(attr):
            def bind_tools_wrapper(*args, **kwargs):
                return _BoundLLMProxy(attr(*args, **kwargs))
            return bind_tools_wrapper
        if name in self._WRAP_METHODS and callable(attr):
            # async generator（astream）：包一层限流 + token 记录。
            # 流式响应是生产主路径，原先完全透传 = 无限流、无用量统计。
            # 注意：流中途失败无法安全重试（会重复输出已消费的 chunk），
            # 韧性链（重试/熔断 fallback）仅覆盖非流式路径。
            if inspect.isasyncgenfunction(attr):
                async def astream_wrapper(*args, **kwargs):
                    from backend.infra.llm.budget import reserve_model_call

                    user_id = kwargs.get("user_id") or _thread_local_user_id()
                    _enforce_rate_limit(user_id)
                    reserve_model_call("primary")
                    _t0 = time.monotonic()
                    usage_chunk = None
                    try:
                        async for chunk in attr(*args, **kwargs):
                            # 携带 usage_metadata 的 chunk（通常为最后一个）留作用量记录
                            if getattr(chunk, "usage_metadata", None):
                                usage_chunk = chunk
                            yield _wrap_result(chunk)
                    finally:
                        # 正常结束或客户端中断都记录（finally 在 generator close 时也执行）
                        if usage_chunk is not None:
                            _record_tokens(
                                usage_chunk,
                                duration_ms=(time.monotonic() - _t0) * 1000,
                            )
                return astream_wrapper
            # sync generator（stream）：修好此前走通用 wrapper 的坏路径
            # （generator 未消费就被 _record_tokens，token 清空），
            # 并包上限流 + token 记录 + 首 chunk 前重试。
            # 注意：不做 sink 增量转发——emit 责任在调用方（RAG 链要过滤
            # META 尾部、reporter 聚合后转发；proxy 自动转发会双发 + 泄漏 META）。
            # 流中途失败无法安全重试（会重复已输出内容）——
            # 仅"第一个内容 chunk 之前"的瞬时错误整体重试；输出后失败直接抛。
            if inspect.isgeneratorfunction(attr):
                def stream_wrapper(*args, **kwargs):
                    from backend.infra.llm.budget import reserve_model_call

                    user_id = kwargs.get("user_id") or _thread_local_user_id()
                    _enforce_rate_limit(user_id)
                    _t0 = time.monotonic()
                    usage_chunk = None
                    yielded_content = False
                    try:
                        for attempt in range(LLM_MAX_RETRIES + 1):
                            reserve_model_call(
                                "primary" if attempt == 0 else "retry"
                            )
                            try:
                                for chunk in attr(*args, **kwargs):
                                    if getattr(chunk, "usage_metadata", None):
                                        usage_chunk = chunk
                                    wrapped = _wrap_result(chunk)
                                    if extract_chunk_text(wrapped):
                                        yielded_content = True
                                    yield wrapped
                                break  # 正常结束
                            except Exception as e:
                                if yielded_content or not _is_transient(e) \
                                        or attempt >= LLM_MAX_RETRIES:
                                    raise
                                delay = LLM_RETRY_BACKOFF_BASE ** (attempt + 1)
                                logger.warning(
                                    f"[LLM:stream] 首 chunk 前瞬时错误，重试 "
                                    f"{attempt + 1}/{LLM_MAX_RETRIES} "
                                    f"({type(e).__name__}, {delay:.1f}s 后)")
                                time.sleep(delay)
                    finally:
                        # 正常结束 / 中止 / 客户端断开都记录（finally 在
                        # generator close 时也执行），与 astream 包装对齐
                        if usage_chunk is not None:
                            _record_tokens(
                                usage_chunk,
                                duration_ms=(time.monotonic() - _t0) * 1000,
                            )
                return stream_wrapper
            # async 方法（ainvoke/agenerate）：coroutine 必须先 await 才能取结果，
            # 否则 _record_tokens 作用在未执行的 coroutine 上会把 token 清空（既有 bug）。
            if inspect.iscoroutinefunction(attr):
                async def async_wrapper(*args, **kwargs):
                    # 限流执行 + 熔断/重试/fallback（P1-7 韧性链）
                    user_id = kwargs.get("user_id") or _thread_local_user_id()
                    _enforce_rate_limit(user_id)
                    _t0 = time.monotonic()
                    result = await _acall_with_resilience(attr, *args, **kwargs)
                    _record_tokens(result, duration_ms=(time.monotonic() - _t0) * 1000)
                    return _wrap_result(result)
                return async_wrapper
            def wrapper(*args, **kwargs):
                # 限流执行 + 熔断/重试/fallback（P1-7 韧性链）
                user_id = kwargs.get("user_id") or _thread_local_user_id()
                _enforce_rate_limit(user_id)
                _t0 = time.monotonic()
                result = _call_with_resilience(attr, *args, **kwargs)
                _record_tokens(result, duration_ms=(time.monotonic() - _t0) * 1000)
                return _wrap_result(result)
            return wrapper
        return attr

    def __call__(self, *args, **kwargs):
        # 限流执行 + 熔断/重试/fallback（P1-7 韧性链）
        user_id = kwargs.get("user_id") or _thread_local_user_id()
        _enforce_rate_limit(user_id)
        _t0 = time.monotonic()
        result = _call_with_resilience(_resolve_active_llm().invoke, *args, **kwargs)
        _record_tokens(result, duration_ms=(time.monotonic() - _t0) * 1000)
        return _wrap_result(result)

    def __repr__(self) -> str:
        try:
            target = _resolve_active_llm()
            return f"<LLMProxy -> {type(target).__name__}({getattr(target, 'model', '?')})>"
        except Exception:
            return "<LLMProxy>"

    def __str__(self) -> str:
        return self.__repr__()


# 公开名
llm = _LLMProxy()


def get_llm() -> BaseChatModel:
    """显式获取当前 LLM 实例"""
    return _resolve_active_llm()
