"""LLM 元数据富化 — invoke_metadata_llm + 摘要工具函数。

历史说明（2026-09-16 清理）：原合并路径 enrich_metadata_llm（关键词+摘要+
实体+模拟问题一次调用）在 F1 重构后失去全部调用方，由 metadata_llm.py
（统一元数据抽取）+ question_gen.py（独立问题生成）取代，已删除。
"""
from __future__ import annotations

import re
import time

from backend.shared.logger import logger


def invoke_metadata_llm(prompt: str, llm_obj=None, *, role: str = "metadata_extract"):
    """RAG 元数据提取专用 LLM 调用 — qwen3 混合思考模型关闭思考模式。

    qwen3.x-plus/max 默认开启思考（thinking budget ~4k token），结构化提取
    （关键词/摘要/分类仲裁）不需要推理链：实测单次调用 8.5s → 1.3s（-85%）。
    仅对 qwen 云模型传 extra_body；DeepSeek/Ollama 不识别该参数，保持原调用。

    Args:
        prompt: 提示词
        llm_obj: 可选 LLM 对象。调用方传入后保持测试和本地模型兼容；
            None 时按 role 解析专用模型角色。
        role: 模型角色，入库阶段默认 ``metadata_extract``。

    Returns: LLM 消息对象（与 llm.invoke 一致）
    Raises: 与 llm.invoke 一致（调用方自行 try/except 降级）
    """
    from backend.infra.llm.proxy import _get_provider_for

    if llm_obj is None:
        from backend.infra.llm.proxy import get_llm_for_role
        llm_obj = get_llm_for_role(role)

    started = time.monotonic()

    def _invoke_and_record(*args, **kwargs):
        response = llm_obj.invoke(*args, **kwargs)
        # get_llm_for_role 返回的是底层 LangChain 实例，不经过 _LLMProxy；
        # 这里补一次统一计量。显式传入的 proxy 自己已记录，避免重复落库。
        if type(llm_obj).__name__ not in {"_LLMProxy", "_BoundLLMProxy"}:
            try:
                from backend.infra.llm.proxy import record_llm_result

                record_llm_result(
                    response,
                    duration_ms=(time.monotonic() - started) * 1000,
                    model_name=str(getattr(llm_obj, "model", "") or ""),
                )
            except Exception as exc:
                logger.debug(f"[MetadataLLM] 直接调用计量失败: {exc}")
        return response

    try:
        # provider 判断基于 llm_obj 自身（proxy 的 __getattr__ 委托到 active llm；
        # 测试 FakeLLM 无 model 属性 → "" → 非 qwen → 走普通 invoke 签名）。
        # qwen（DashScope）与 siliconflow 均支持顶层 enable_thinking——实测
        # 硅基流动 Qwen3-8B 默认思考 14.3s/727 字，关闭后 0.8s（2026-09-19）。
        model_name = str(getattr(llm_obj, "model", "") or "")
        if _get_provider_for(model_name) in ("qwen", "siliconflow"):
            return _invoke_and_record(
                prompt, extra_body={"enable_thinking": False}
            )
    except Exception as e:  # 解析失败不影响主流程，按普通调用
        logger.debug(f"[MetadataLLM] 模型解析失败，走普通调用: {e}")
    return _invoke_and_record(prompt)


def _extract_first_sentences(text: str, n: int = 2) -> str:
    """抽取式摘要: 优先剥 markdown 标记再按句切分, 取前 n 句."""
    if not text:
        return ""
    import re as _re
    t = text.strip()
    if t.startswith("{") and t.endswith("}"):
        try:
            import json as _json
            data = _json.loads(t)
            if isinstance(data, dict) and isinstance(data.get("summary"), str):
                inner = data["summary"].strip()
                if inner and len(inner) > 4:
                    text = inner
        except (_json.JSONDecodeError, ValueError):
            # 不是合法 JSON 摘要包裹 → 按纯文本继续抽取（策略链 fallback），无需日志
            pass
    text = _re.sub(r"^#{1,6}\s+", "", text, flags=_re.MULTILINE)
    text = _re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = _re.sub(r"^\s*[-*+]\s+", "", text, flags=_re.MULTILINE)
    parts = _re.split(r"[。！？!?.\s]+", text)
    parts = [p.strip() for p in parts if p and len(p.strip()) > 4][:n]
    if not parts:
        return text[:150].strip()
    summary = "。".join(parts)
    if not summary.endswith("。"):
        summary += "。"
    return summary[:200]


def _smart_truncate(text: str, max_length: int) -> str:
    """智能截断文本，尽量在句号、换行处截断，避免切断单词或乱码。"""
    if len(text) <= max_length:
        return text
    for sep in (". ", "\n\n", "\n", "。", "；", "，", " "):
        idx = text.rfind(sep, 0, max_length)
        if idx > max_length * 0.7:
            return text[:idx].strip()
    return text[:max_length].strip()
