"""LLM 元数据富化 — invoke_metadata_llm + 摘要工具函数。

历史说明（2026-09-16 清理）：原合并路径 enrich_metadata_llm（关键词+摘要+
实体+模拟问题一次调用）在 F1 重构后失去全部调用方，由 metadata_llm.py
（统一元数据抽取）+ question_gen.py（独立问题生成）取代，已删除。
"""
from __future__ import annotations

import time

from backend.shared.logger import logger


def invoke_metadata_llm(
    prompt: str,
    llm_obj=None,
    *,
    role: str = "metadata_extract",
    max_tokens: int | None = None,
):
    """RAG 元数据提取专用 LLM 调用 — qwen3 混合思考模型关闭思考模式。

    qwen3.x-plus/max 默认开启思考（thinking budget ~4k token），结构化提取
    （关键词/摘要/分类仲裁）不需要推理链：实测单次调用 8.5s → 1.3s（-85%）。
    仅对 qwen 云模型传 extra_body；DeepSeek/Ollama 不识别该参数，保持原调用。

    Args:
        prompt: 提示词
        llm_obj: 可选 LLM 对象。调用方传入后保持测试和本地模型兼容；
            None 时按 role 解析专用模型角色。
        role: 模型角色，入库阶段默认 ``metadata_extract``。
        max_tokens: Provider 支持的生成上限；默认角色通过统一代理绑定，
            显式传入的模型对象则使用其标准 ``bind`` 接口。

    Returns: LLM 消息对象（与 llm.invoke 一致）
    Raises: 与 llm.invoke 一致（调用方自行 try/except 降级）
    """
    if llm_obj is None:
        # 元数据角色也必须走统一代理，确保预算预占、韧性处理和用量结算
        # 与普通对话一致。get_llm_for_role 返回裸 LangChain 实例，会绕过这些门禁。
        from backend.infra.llm.proxy import bind_llm_for_role
        llm_obj = bind_llm_for_role(role, max_tokens=max_tokens)
    elif max_tokens is not None:
        binder = getattr(llm_obj, "bind", None)
        if not callable(binder):
            raise TypeError("显式 LLM 对象不支持 max_tokens 参数绑定")
        llm_obj = binder(max_tokens=max_tokens)

    started = time.monotonic()

    def _invoke_and_record(*args, **kwargs):
        if type(llm_obj).__name__ not in {"_LLMProxy", "_BoundLLMProxy"}:
            from backend.infra.llm.proxy import enforce_model_input_price_tier

            # 只有"显式传入原始客户端"（测试/兼容）才会走到这里：没有
            # canonical 角色名，按实例 model 做价格档位校验；角色代理路径
            # 由代理内部完成预占与结算，不进这个分支。
            model_name = str(getattr(llm_obj, "model", "") or "")
            enforce_model_input_price_tier(args, model_name=model_name)
        response = llm_obj.invoke(*args, **kwargs)
        # 默认角色代理及显式传入的治理代理已自行预占和记账；兼容旧调用方
        # 显式传入的原始 LangChain 对象时，保留原有用量遥测。
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
        # STOP D（D4）：thinking 开关判定统一走 Registry 口径
        # （models.supports_thinking_flag：capabilities 显式声明优先 + legacy
        # 兼容）。实测背景：qwen 系默认思考 14.3s/727 字，摘要关闭后 0.8s。
        model_name = str(getattr(llm_obj, "model", "") or "")
        from backend.infra.llm.models import supports_thinking_flag

        if supports_thinking_flag(model_name):
            return _invoke_and_record(
                prompt, extra_body={"enable_thinking": False}
            )
    except Exception as e:  # 解析失败不影响主流程，按普通调用
        logger.debug(f"[MetadataLLM] 模型解析失败，走普通调用: {e}")
    return _invoke_and_record(prompt)


def _extract_first_sentences(text: str, n: int = 2) -> str:
    """抽取式摘要: 优先剥 markdown 标记再按句切分, 取前 n 句.

    2026-10-19 修复（faq_only.docx 实证）：旧实现按
    ``[。！？!?.\\s]+`` 切分——空格与半角句号被当句界，"A：请在 OA 系统
    提交年假申请"被切成碎片，>4 字过滤后再把不相邻碎片拼成残缺摘要
    （"Q：年假怎么申请。系统提交年假申请，经直属上级审批、HR。备案后
    效。"）。现改为：① QA 结构文本优先取第一个完整问答对（不拆问、
    不截答案）；② 通用路径仅按真正句终符零宽切句（句内空格与半角点
    不再被误切）。
    """
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
    # QA 结构优先：完整问答对是天然的摘要单元
    qa = _re.match(r"\s*Q\s*[：:]\s*(.+?)\s*\n+\s*A\s*[：:]\s*(.+)", text)
    if qa:
        q = qa.group(1).strip().rstrip("？?。")
        a = qa.group(2).strip().splitlines()[0].strip()
        if q and a:
            return f"Q：{q}？ A：{a}"[:200]
    # 零宽断言按句终符切句：句内空格、缩写半角点不再被误切
    parts = [p.strip() for p in _re.split(r"(?<=[。！？!?；;])\s*", text)
             if p and len(p.strip()) > 4][:n]
    if not parts:
        return text[:150].strip()
    summary = "".join(parts)
    if not summary.endswith(("。", "！", "？", "；", ";", "!", "?")):
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
