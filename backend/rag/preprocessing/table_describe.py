"""表格 chunk LLM 描述（4.3c）— 为行级 kv chunk 生成一句话语义描述。

背景：行级 kv chunk（"科目 货币资金 / Q3金额 1234567"）对 embedding 是
语义稀疏的数字序列，检索命中依赖词面重叠。本模块为表格行批量生成
"该行记录了什么" 的一句话描述，拼入 embedding 前缀【表格】段，
弥合「自然语言提问 ↔ 结构化数据行」的语义鸿沟。

成本控制：
- 只对 chunk_type=table_row 的 chunk 调用（量小）
- 每文档上限 TABLE_DESC_MAX_ROWS 行，超出部分无描述（前缀自动跳段）
- 一次 LLM 调用产出全部行描述（对齐 question_gen 模式）
- 走 invoke_metadata_llm（proxy 自动计量）；失败降级为空描述
"""
from __future__ import annotations

import json
import re

from backend.config.rag import ENABLE_TABLE_DESCRIPTIONS, TABLE_DESC_MAX_ROWS
from backend.shared.logger import logger

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _invoke_llm(prompt: str, llm_obj=None):
    """LLM 调用桩（测试 monkeypatch 点）——走 proxy 自动计量。"""
    from backend.rag.preprocessing.llm_enrichment import invoke_metadata_llm
    return invoke_metadata_llm(prompt, llm_obj=llm_obj)


def _build_prompt(kv_texts: list[str], table_summary: str) -> str:
    lines = "\n".join(f"[行{i}] {kv[:200]}" for i, kv in enumerate(kv_texts))
    return (
        "你是知识库检索索引构建助手。以下是一张表格中的若干数据行"
        "（kv 格式：列名 值）。\n"
        f"表级信息：{table_summary or '（无）'}\n\n"
        f"{lines}\n\n"
        "请为每一行生成一句自然的中文描述（20-40 字），说明该行数据"
        "记录了什么业务含义，便于用自然语言检索到它。\n"
        "只输出 JSON：{\"descriptions\": [\"...\", ...]}，"
        f"数组长度必须等于 {len(kv_texts)}。"
    )


def generate_table_descriptions(kv_texts: list[str],
                                table_summary: str = "") -> dict[int, str]:
    """为行级 kv chunk 批量生成描述。

    Returns:
        {行索引: 描述}；功能关闭/失败/长度不符时返回 {}（调用方无前缀）。
    """
    if not ENABLE_TABLE_DESCRIPTIONS or not kv_texts:
        return {}

    scope = kv_texts[:TABLE_DESC_MAX_ROWS]
    prompt = _build_prompt(scope, table_summary)
    try:
        result = _invoke_llm(prompt)
        content = result.content.strip() if hasattr(result, "content") else str(result)
        match = _JSON_RE.search(content)
        if not match:
            return {}
        data = json.loads(match.group())
        raw = data.get("descriptions")
        if not isinstance(raw, list) or len(raw) != len(scope):
            logger.warning(
                "[TableDesc] descriptions 长度不符 (%s/%s)，放弃描述",
                len(raw) if isinstance(raw, list) else 0, len(scope),
            )
            return {}
        cleaned = {
            i: str(d).strip()[:80]
            for i, d in enumerate(raw)
            if str(d).strip() and len(str(d).strip()) >= 4
        }
        logger.info(f"[TableDesc] 生成 {len(cleaned)}/{len(scope)} 行描述")
        return cleaned
    except Exception as e:
        logger.warning(f"[TableDesc] LLM 调用失败（无描述降级）: {type(e).__name__}: {e}")
        return {}
