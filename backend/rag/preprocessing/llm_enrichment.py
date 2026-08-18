"""LLM 元数据富化 — 合并关键词+摘要+实体+模拟问题为一次 LLM 调用。

从 metadata.py 抽出（PR-2.x 分解）。
"""
from __future__ import annotations

import json as _json
import re
from typing import Optional

from backend.shared.logger import logger


def _fallback_questions(chunk_text: str) -> list[str]:
    """LLM 未生成问题或长度不符时的兜底 — 给 1 个无害占位问句。

    设计原则：fallback 必须"无害"（不引入噪声）而非"有用"。
    宁可让该 chunk 召回率低，也不要因为错误问题把不相关问句召回来。
    """
    import re as _re
    first_sentence = _re.split(r'[。！？；\n]', chunk_text.strip(), maxsplit=1)[0].strip()
    if not first_sentence:
        return []
    return ["这段内容讲什么？"]


def enrich_metadata_llm(text: str, doc_type: str, chunks_text: list[str] | None = None) -> dict:
    """合并关键词+摘要+实体(+ 模拟问题)为一次 LLM 调用（省 N 次 API 往返）。

    Args:
        text: 文档全文（或摘要采样）
        doc_type: 文档类型
        chunks_text: chunk 文本列表（提供时同时生成每 chunk 的模拟问题；为 None 时不生成）

    Returns:
        {"keywords": [...], "summary": "...", "entities": [...], "tokens": {...},
         "questions_by_chunk": [[q1, q2, q3], ...]}  # 长度 == len(chunks_text)
    失败返回空 dict，调用方降级到独立调用。
    """
    from backend.config.rag import DOC_LLM_MODEL
    from backend.config import LLM_MODEL

    safe_text = text

    # chunks_text 提供时，附加编号预览到 prompt，让 LLM 按 chunk 编号生成问题
    chunks_block = ""
    questions_field = ""
    extra_schema = ""
    if chunks_text:
        chunks_block = "\n\n文档已切分为以下 chunks（编号从 0 开始）:\n"
        for idx, ct in enumerate(chunks_text):
            preview = ct[:300].replace("\n", " ")
            chunks_block += f"\n[Chunk #{idx}]\n{preview}...\n"
        questions_field = (
            f"- simulated_questions 是数组,长度必须等于 chunks 数量({len(chunks_text)})。"
            f"每个元素是该 chunk 的 2-3 个模拟用户提问(必须严格基于该 chunk 内容,口语化疑问句,"
            f"只问'是什么/怎么样/标准是什么/如何定义')"
        )
        extra_schema = ', "simulated_questions": [["<问题 1>", "<问题 2>"], ...]'

    prompt = f"""你是专业 RAG 元数据提取专家。从文档提取 1 个 JSON 对象,严禁任何额外内容。

严格规则:
- summary 必须是 1-3 句中文摘要,严格控制在 200 个汉字以内(超出部分将被系统强制截断,视为不合格输出)
  纯自然语言,严禁用 markdown 标题符号/列表/换行/星号
- keywords 是 10 个以内的关键词数组
- entities 是 5 个以内的实体数组(每项含 name 和 type 字段,type 取 regulation/person/platform/brand)
{questions_field}
- 严禁在 JSON 外加解释、严禁使用 markdown 围栏、严禁重复文档原文当摘要

JSON Schema:
{{"summary": "<1-3 句中文摘要,<=200字>", "keywords": ["<词 1>", ...], "entities": [{{"name":"<名称>","type":"<类型>"}}, ...]{extra_schema}}}

文档:
{safe_text}
{chunks_block}

只返回这个 JSON,无其他文字:"""

    try:
        if DOC_LLM_MODEL:
            from langchain_ollama import ChatOllama
            llm = ChatOllama(model=DOC_LLM_MODEL, temperature=0.0, num_ctx=4096, request_timeout=60)
            result = llm.invoke(prompt)
            model_used = DOC_LLM_MODEL
            tokens = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0, "model": model_used}
        else:
            from backend.infra.llm import llm
            from backend.infra.llm.proxy import _last_call_meta_var
            _last_call_meta_var.set({})  # 清理旧记录（ContextVar 不可变替换）
            result = llm.invoke(prompt)
            model_used = LLM_MODEL
            _meta = _last_call_meta_var.get()
            tokens = {
                "prompt_tokens": _meta.get("prompt_tokens", 0),
                "completion_tokens": _meta.get("completion_tokens", 0),
                "cost_usd": _meta.get("cost_usd", 0),
                "model": model_used,
            }

        content = result.content.strip() if hasattr(result, "content") else str(result).strip()
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            data = _json.loads(match.group())
            keywords = data.get("keywords", [])
            if isinstance(keywords, list):
                keywords = [str(k).strip() for k in keywords if str(k).strip() and len(str(k).strip()) > 1][:10]
            summary = _smart_truncate(str(data.get("summary", "")), 200)
            if summary and (summary.startswith("#") or "**" in summary):
                logger.warning("[Enrich LLM] summary 疑似原文 fallback, 改用抽取式")
                summary = _extract_first_sentences(safe_text, 2)
            entities = data.get("entities", [])
            if not isinstance(entities, list):
                entities = []

            questions_by_chunk: list[list[str]] = []
            if chunks_text:
                raw_q = data.get("simulated_questions", [])
                expected_len = len(chunks_text)
                if isinstance(raw_q, list) and len(raw_q) == expected_len:
                    for i, qs in enumerate(raw_q):
                        if isinstance(qs, list):
                            cleaned = [str(q).strip() for q in qs
                                       if str(q).strip() and len(str(q).strip()) >= 4][:3]
                            questions_by_chunk.append(cleaned if cleaned else _fallback_questions(chunks_text[i]))
                        else:
                            questions_by_chunk.append(_fallback_questions(chunks_text[i]))
                else:
                    logger.warning(
                        f"[Enrich LLM] simulated_questions 缺失或长度不符 "
                        f"({len(raw_q) if isinstance(raw_q, list) else 0}/{expected_len}), 全部 fallback"
                    )
                    questions_by_chunk = [_fallback_questions(ct) for ct in chunks_text]

            logger.info(
                f"[Enrich LLM] {model_used} → {len(keywords)}kw + {len(summary)}字摘要 + "
                f"{len(entities)}实体 + {len(questions_by_chunk)}chunks问题"
            )
            return {
                "keywords": keywords,
                "summary": summary,
                "entities": entities,
                "tokens": tokens,
                "questions_by_chunk": questions_by_chunk,
            }
        else:
            lines = [l.strip() for l in content.split("\n") if l.strip() and len(l.strip()) > 1]
            kws = [l for l in lines if not l.startswith("{")][:10]
            return {
                "keywords": kws,
                "summary": _extract_first_sentences(content, 2),
                "entities": [],
                "tokens": tokens,
                "questions_by_chunk": [_fallback_questions(ct) for ct in chunks_text] if chunks_text else [],
            }
    except Exception as e:
        logger.warning(f"[Enrich LLM] 失败: {e}")
        return {}


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
