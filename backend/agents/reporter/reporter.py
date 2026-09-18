"""
reporter.py — 最终 Markdown 回答生成 + LangGraph 节点适配

职责:
  - reporter_node(): LangGraph 节点适配器（state → generate_final_answer）
  - generate_final_answer(): 纯内容生成（汇总 step_results → LLM → Markdown）
  - Context Filter 过滤无关 RAG 结果
  - 引用/参考文献提取

不依赖:
  - FastAPI / SSE
  - 数据库 / 向量库
"""

from backend.infra.llm import llm
from langchain_core.messages import AIMessage
from backend.shared.logger import logger
from backend.agents.reporter.context_filter import filter_step_results
from backend.prompts.service import prompt_service


# =====================================================
# LangGraph 节点适配器
# =====================================================

def reporter_node(state: dict) -> dict:
    """LangGraph 节点适配器: state → generate_final_answer → {"final_answer": ...}"""
    # ── L1 弱命中追问（2026-09-19 拒答转追问）：clarify 模式下不执行任何
    # 汇总/LLM 逻辑；追问卡片事件已由 router 节点原始输出发出，这里只出
    # 一句如实告知的短文案（"_clarify" 不重复附带，防双卡片）
    if state.get("route_mode") == "clarify":
        from backend.orchestration.graph.clarify_content import (
            CLARIFY_STANDALONE_TEXT,
        )

        return {"final_answer": CLARIFY_STANDALONE_TEXT}

    question = state.get("question", "")
    step_results = state.get("step_results", {})

    answer = generate_final_answer(
        question=question,
        step_results=step_results,
        context_filter=True,
    )

    # ── L2 拒答兜底追问：所有步骤都无有效产出（RAG 拒答话术/空结果）且
    # 无技术性错误时，在节点原始输出附带 _clarify（events.py 据此发
    # clarification 事件）。拒答正文照常返回，不做静默替换。
    clarify = _refusal_clarify_marker(answer, state)
    if clarify is not None:
        return {"final_answer": answer, "_clarify": clarify}
    return {"final_answer": answer}


def _refusal_clarify_marker(answer: str, state: dict) -> dict | None:
    """判定本回答是否为"业务性拒答"，是则返回追问标记。

    判定口径：generate_final_answer 的"无有效输出"分支固定以「## 抱歉」开头，
    技术性错误（服务暂时不可用）同走该分支但不属于拒答，须排除——
    两者都是本模块自有模板，前缀匹配是模块内稳定契约。
    延迟导入 clarify_content（调用期取开关/守卫，便于测试替换）。
    """
    if not answer.startswith("## 抱歉") or "服务暂时不可用" in answer:
        return None

    try:
        from backend.config import REFUSAL_CLARIFY_ENABLED
        from backend.orchestration.graph.clarify_content import (
            build_refusal_clarify,
            clarify_allowed,
            mark_clarified,
        )

        if not REFUSAL_CLARIFY_ENABLED:
            return None
        session_id = state.get("session_id", "")
        if not clarify_allowed(session_id):
            return None
        mark_clarified(session_id)
        return build_refusal_clarify(
            state.get("question", ""), state.get("domain_hint", ""))
    except Exception as e:
        logger.warning(f"[Reporter] 拒答追问判定失败，输出原拒答: {e}")
        return None


# =====================================================
# 核心生成函数
# =====================================================

# capability → 用户可读标签（与 trace_middleware 标签体系对齐）。
# 降级提示是面向用户的文案，绝不能泄漏内部步骤描述
# （如 direct_executor 生成的 "直接执行 sql.query"，浏览器实测发现）。
_CAP_USER_LABELS = {
    "sql.query": "数据库查询",
    "rag.search": "知识库检索",
    "report": "报告生成",
    "business_analysis": "业务分析",
    "workflow": "工作流",
}


def _user_step_label(sr: dict) -> str:
    """step → 用户可读标签；未知 capability 统一为泛称，不泄漏内部命名。"""
    return _CAP_USER_LABELS.get(sr.get("capability", ""), "信息查询")

def generate_final_answer(
    question: str,
    step_results: dict,
    *,
    context_filter: bool = True,
) -> str:
    """
    生成最终 Markdown 回答（纯函数，无副作用）。

    Args:
        question: 原始用户问题
        step_results: {step_id: {capability, description, output, status, ...}}
        context_filter: 是否启用 Context Filter 过滤无关 RAG 结果

    Returns:
        最终 Markdown 格式回答（含参考文献）
    """
    # —— 全部失败 / 无有效输出：直接返回 ——
    all_success = {
        sid: sr for sid, sr in step_results.items()
        if _is_step_successful(sr)
    }
    if not all_success:
        failed_descs = []
        for sid, sr in step_results.items():
            label = _user_step_label(sr)
            err = sr.get("error", "")
            if err and _is_technical_error(err):
                # 技术错误不暴露给用户，只记日志
                logger.error(f"[Reporter] step={sid} 技术错误: {err[:200]}")
                failed_descs.append(f"- {label}: 服务暂时不可用")
            elif err:
                # 业务错误保留提示，但只记日志原始错误（可能含内部细节）
                logger.warning(f"[Reporter] step={sid} 执行失败: {err[:200]}")
                failed_descs.append(f"- {label}: 未找到相关信息")
            else:
                failed_descs.append(f"- {label}: 未找到相关信息")
        logger.info(f"[Reporter] 无有效输出，返回降级提示")
        return (
            f"## 抱歉\n\n"
            f"未能找到与「{question[:60]}」相关的信息。\n\n"
            + "\n".join(failed_descs) +
            f"\n\n建议换个关键词或查阅其他资料。"
        )

    # Context Filter — 仅多步骤时启用（单步骤无交叉过滤意义，省掉 CrossEncoder ~1-2s）
    if context_filter and len(step_results) > 1:
        step_results = filter_step_results(step_results, question)

    # —— 快速路径：RAG 有结果且其他步骤无实质输出时，直接透传 ——
    rag_steps = {
        sid: sr for sid, sr in step_results.items()
        if sr.get("capability") == "rag.search" and sr.get("status") == "success"
        and sr.get("output") and len(str(sr.get("output", ""))) > 50
    }
    other_meaningful = [
        sid for sid, sr in step_results.items()
        if sr.get("capability") != "rag.search"
        and sr.get("output")
        and len(str(sr.get("output", "")).strip()) > 30
        and "无结果" not in str(sr.get("output", ""))
        and "未找到" not in str(sr.get("output", ""))
    ]
    if rag_steps and not other_meaningful:
        rag_output = list(rag_steps.values())[0].get("output", "")
        if rag_output:
            logger.info("[Reporter] RAG 有实质输出且其他步骤无，直接透传")
            return rag_output

    # —— 快速路径：单步骤有实质输出时直接透传（省掉 LLM 总结 ~2s）——
    # 仅限字符串输出（RAG/报告类）；SQL/BusinessInsight 是结构化 dict，
    # 裸透传会变成 dict repr 展示给用户，必须走下方渲染或 LLM 路径
    if len(all_success) == 1:
        sole_sr = list(all_success.values())[0]
        sole_output = sole_sr.get("output", "")
        if isinstance(sole_output, str) and len(sole_output.strip()) > 5:
            logger.info("[Reporter] 单步骤有实质输出，直接透传（跳过 LLM 总结）")
            return sole_output

    # 提取参考文献
    rag_references = _extract_rag_references(step_results)

    # 构建步骤输出摘要
    outputs_text = _format_step_outputs(step_results, strip_references=True)

    if not outputs_text:
        logger.warning("[Reporter] 无可用的 step 输出")
        return "## 抱歉\n\n未能生成报告。所有步骤均未产生有效输出。请检查数据源是否正常。"

    logger.info(f"[Reporter] 汇总 {len(step_results)} 个步骤结果...")

    # ── P2 性能优化：结构化渲染（0ms 模板）+ LLM 一句话总结（~2s）──
    structured = _render_structured_sections(step_results)
    if structured:
        try:
            # LLM 只写一句话执行摘要（max_tokens=64 防超长；模板由 reporter.summary 提供）
            data_summary = _build_data_summary(step_results)
            summary_prompt = prompt_service.render_sync(
                "reporter.summary", data_summary=data_summary
            ).text
            resp = llm.bind(max_tokens=64).invoke(summary_prompt)
            summary = resp.content.strip()
            final = f"## {summary}\n\n{structured}"
            if rag_references:
                final = final + rag_references
            logger.info(f"[Reporter] 结构化报告: {len(final)} 字符")
            return final
        except Exception as e:
            logger.warning(f"[Reporter] LLM 一句话总结失败，降级: {e}")
            final = f"## 数据分析报告\n\n{structured}"
            return final

    # ── 非结构化数据：走完整 LLM 路径（与旧行为一致）──
    try:
        # system 用 reporter.system（含禁编造/引用保留等严格规则）；
        # human 内联构建——此前误用 reporter.summary（变量为 data_summary）
        # 渲染必失败，导致该路径长期落入 _fallback_summary 降级。
        system_r = prompt_service.render_sync("reporter.system")
        human_text = (
            f"## 用户问题\n{question}\n\n"
            f"## 步骤执行结果（回答只能基于以下内容）\n{outputs_text}\n\n"
            f"请生成最终报告:"
        )
        msgs = [
            ("system", system_r.text.strip()),
            ("human", human_text.strip()),
        ]

        # ── P1 真 token 级流式：完整 LLM 路径切 llm.stream，边生成边经 sink
        # 推给 SSE；增量聚合为完整回答，行为与 invoke 路径一致 ──
        from backend.config import ENABLE_TOKEN_STREAMING
        from backend.infra.llm.proxy import (
            emit_stream_delta, extract_chunk_reasoning, extract_chunk_text,
        )
        if ENABLE_TOKEN_STREAMING:
            parts = []
            for chunk in llm.stream(msgs):
                # 思考链增量（推理模型 reasoning_content）走独立 thinking 事件，
                # 不计入回答正文
                reasoning = extract_chunk_reasoning(chunk)
                if reasoning:
                    emit_stream_delta(reasoning, kind="thinking")
                text = extract_chunk_text(chunk)
                if text:
                    parts.append(text)
                    emit_stream_delta(text)
            resp = AIMessage(content="".join(parts))
        else:
            resp = llm.invoke(msgs)
        final = resp.content.strip()

        if rag_references:
            final = final + rag_references

        logger.info(f"[Reporter] 最终报告: {len(final)} 字符")
        return final

    except Exception as e:
        logger.error(f"[Reporter] 汇总失败: {e}")
        return _fallback_summary(question, step_results, str(e))


# =====================================================
# 辅助函数
# =====================================================

def _is_technical_error(error: str) -> bool:
    """判断错误是否为技术性错误（不应暴露给用户）。"""
    tech_patterns = [
        "Expected where value", "ChromaDB", "chromadb",
        "psycopg2", "connection", "timeout",
        "SQLSTATE", "syntax error", "Traceback",
        "ModuleNotFoundError", "ImportError",
    ]
    return any(p.lower() in error.lower() for p in tech_patterns)


def _is_step_successful(result: dict) -> bool:
    """检查步骤是否真正成功（结构化判断）"""
    if result.get("status") != "success":
        return False
    if result.get("is_empty"):
        return False
    if result.get("error_type"):
        return False
    # workflow executor 直接产出最终答案，始终视为成功
    if result.get("capability") == "workflow":
        return True
    output = str(result.get("output", ""))
    # 降门槛：5 字符即可（原 20 字符过于严格，RAG 短摘要被误杀）
    if len(output.strip()) <= 5:
        return False
    # EvidenceGate 拒答/空话术不是有效产出（实测 2026-09-15：RAG 拒答短语
    # "知识库暂无相关资料。" 长 10 字符绕过长度门槛，被当成有效结果透传，
    # 最终回答只剩这一句，SQL 空结果完全没交代）
    if _is_empty_output(output):
        return False
    return True


# Skill 层的"标准空结果"话术——内容为空但 status=success 的产出。
# 命中即视为该步骤"未获得数据"，Reporter 汇总时必须显式交代，不得透传。
_EMPTY_RESULT_PHRASES = frozenset({
    "知识库暂无相关资料。",
    "知识库暂无相关资料",
    "未找到相关信息。",
    "未找到相关信息",
    "无结果",
    "未能获取任何有效数据。",
})
# 前缀变体：RAG self-correction 会在拒答话术后追加改写说明
# （"知识库暂无相关资料。\n（已尝试改写提问重新检索，仍未找到可靠答案）"），
# 精确匹配拦不住，被当有效结果透传（实测 2026-09-19，拒答转追问由此漏判）
_EMPTY_RESULT_PREFIXES = ("知识库暂无相关资料", "未找到相关信息")


def _is_empty_output(output: str) -> bool:
    """空结果话术判定：精确短语 + 带后缀的前缀变体。"""
    text = output.strip()
    if text in _EMPTY_RESULT_PHRASES:
        return True
    return any(text.startswith(p) for p in _EMPTY_RESULT_PREFIXES)


def _extract_rag_references(step_results: dict) -> str:
    """从 search_knowledge 步骤的输出中提取参考文献部分"""
    import re
    from backend.agents.reporter.context_filter import parse_sources_from_text

    all_refs = []
    seen_files = set()

    for sid, sr in step_results.items():
        if sr.get("capability") != "rag.search":
            continue
        if sr.get("status") != "success" or sr.get("_filtered"):
            continue

        output = str(sr.get("output", ""))
        for marker in ["### 参考文献", "### 参考来源"]:
            idx = output.find(marker)
            if idx != -1:
                ref_section = output[idx:]
                ref_entries = []
                for line in ref_section.split("\n"):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or stripped.startswith("---"):
                        ref_entries.append(line)
                        continue
                    m = re.match(r'\d+\.\s*\*\*(.+?)\*\*', stripped)
                    if m:
                        fname = m.group(1)
                        if fname not in seen_files:
                            seen_files.add(fname)
                            ref_entries.append(line)
                    elif seen_files:
                        ref_entries.append(line)
                if ref_entries:
                    all_refs.append("\n".join(ref_entries))
                break

    if not all_refs:
        return ""
    return "\n\n" + "\n\n".join(all_refs)


def _extract_sources_from_steps(step_results: dict) -> list[dict]:
    """从 RAG step_results 中提取结构化来源"""
    from backend.agents.reporter.context_filter import parse_sources_from_text

    for sr in step_results.values():
        if sr.get("capability") != "rag.search":
            continue
        if sr.get("status") != "success" or sr.get("_filtered"):
            continue
        output = str(sr.get("output", ""))
        sources = parse_sources_from_text(output)
        if sources:
            return sources

    for sr in step_results.values():
        output = str(sr.get("output", sr.get("final_answer", "")))
        sources = parse_sources_from_text(output)
        if sources:
            return sources

    return []


def _format_step_outputs(step_results: dict[str, dict], strip_references: bool = False) -> str:
    """将 step_results 格式化为 LLM 可读的文本"""
    parts = []
    for step_id, sr in sorted(step_results.items()):
        status = sr.get("status", "unknown")
        description = sr.get("description", step_id)
        capability = sr.get("capability", "")

        header = f"### 步骤 {step_id}: {description}"
        if status == "success":
            output = str(sr.get("output", ""))
            if strip_references and capability == "rag.search":
                for marker in ["\n\n---\n\n### 参考文献", "\n\n---\n\n### 参考来源"]:
                    idx = output.find(marker)
                    if idx != -1:
                        output = output[:idx] + "\n\n*(参考文献已移至报告末尾)*"
                        break
            if len(output) > 3000:
                output = output[:3000] + "\n\n*(输出过长，已截断)*"
            parts.append(f"{header}\n状态: ✅ 成功 ({capability})\n\n{output}\n")
        elif status == "failed":
            parts.append(f"{header}\n状态: ❌ 失败 ({capability})\n错误: {sr.get('error', '未知错误')}\n")
        elif status == "skipped":
            parts.append(f"{header}\n状态: ⏭️ 已跳过 ({capability})\n原因: {sr.get('error', '')}\n")
        else:
            parts.append(f"{header}\n状态: ⏳ {status}\n")
    return "\n".join(parts) if parts else ""


def _render_structured_sections(step_results: dict) -> str:
    """对结构化数据（SQLResult dict + BusinessInsight dict）进行模板渲染。

    返回 Markdown 字符串，或 ""（数据非结构化时回退到 LLM 路径）。

    实测整改（2026-09-15）：SQL 0 行结果此前被静默跳过（if columns and rows），
    配合 RAG 拒答话术透传，最终回答只剩一句"知识库暂无相关资料"，用户完全
    看不到"查了什么、查到没有"。现在：
      - SQL 成功但 0 行 → 显式渲染"查询成功但无数据"说明节；
      - 失败 / 被跳过 / 空话术的步骤 → 渲染"未获得数据"交代节；
    只要有任一节就返回，不再要求 >=2 节。
    """
    sections = []
    for step_id, sr in sorted(step_results.items()):
        status = sr.get("status", "unknown")
        output = sr.get("output")
        capability = sr.get("capability", "")
        description = sr.get("description", step_id)

        if status == "success" and isinstance(output, dict):
            # SQLResult → 表格（0 行也渲染说明，不再静默丢弃）
            if "columns" in output and "rows" in output and capability == "sql.query":
                columns = output.get("columns", [])
                rows = output.get("rows", [])
                if rows:
                    section = _render_table_section(description, columns, rows)
                else:
                    section = (
                        f"### {description}\n\n"
                        f"查询执行成功，但未返回任何数据（0 行）。\n\n"
                        f"可能原因：筛选条件下当前无匹配记录，或相关表暂无数据。"
                    )
                sections.append(section)
            # BusinessInsight → 风险+建议
            elif "summary" in output and "risks" in output:
                section = _render_insight_section(description, output)
                sections.append(section)
            continue

        # 非成功 / 空话术步骤：显式交代（原实现完全隐身）
        if status != "success":
            reason = sr.get("error", "") or "步骤未执行成功"
            label = _user_step_label(sr)
            sections.append(
                f"### {description}\n\n"
                f"- {label}未获得数据：{reason[:120]}"
            )
        elif isinstance(output, str) and _is_empty_output(output):
            label = _user_step_label(sr)
            sections.append(
                f"### {description}\n\n"
                f"- {label}未找到相关内容（数据源中无匹配信息）。"
            )

    # 有任一节就走结构化路径（原 >=2 门槛导致单节被丢弃、回退弱 LLM 路径）
    if sections:
        return "\n\n---\n\n".join(sections)
    return ""


def _render_table_section(description: str, columns: list[str], rows: list[dict]) -> str:
    """渲染 SQL 结果为 Markdown 表格。"""
    # 表头
    header = "| " + " | ".join(str(c) for c in columns) + " |"
    sep = "|" + "|".join(":---:" for _ in columns) + "|"

    # 数据行（最多 20 行）
    display_rows = rows[:20]
    data_lines = []
    for row in display_rows:
        vals = [str(row.get(c, "")) for c in columns]
        data_lines.append("| " + " | ".join(vals) + " |")

    lines = [
        f"### {description}",
        "",
        header,
        sep,
    ] + data_lines

    if len(rows) > 20:
        lines.append(f"\n*(共 {len(rows)} 行，仅显示前 20 行)*")

    return "\n".join(lines)


def _render_insight_section(description: str, output: dict) -> str:
    """渲染 BusinessInsight 为风险+建议列表。"""
    lines = [f"### {description}", ""]

    summary = output.get("summary", "")
    if summary:
        lines.append(f"> {summary}")
        lines.append("")

    risks = output.get("risks", [])
    if risks:
        lines.append("**风险:**")
        for r in risks:
            lines.append(f"- ⚠️ {r}")
        lines.append("")

    suggestions = output.get("suggestions", [])
    if suggestions:
        lines.append("**建议:**")
        for s in suggestions:
            lines.append(f"- 💡 {s}")
        lines.append("")

    confidence = output.get("confidence", None)
    if confidence is not None:
        bar = "█" * max(1, int(confidence * 10))
        lines.append(f"*置信度: {bar} {confidence:.0%}*")

    return "\n".join(lines)


def _build_data_summary(step_results: dict) -> str:
    """从结构化结果构建一句话数据摘要（供 LLM 总结用）。"""
    parts = []
    for sr in step_results.values():
        if sr.get("status") != "success":
            continue
        output = sr.get("output")
        if not isinstance(output, dict):
            continue

        row_count = output.get("row_count", 0)
        tables = output.get("tables", [])

        if "risks" in output:
            parts.append(f"风险: {len(output.get('risks',[]))}个")
        if "summary" in output and output["summary"]:
            parts.append(output["summary"][:80])
        if row_count:
            tables_str = ", ".join(tables[:3]) if tables else "数据"
            parts.append(f"查询{tables_str}返回{row_count}行")

    return "; ".join(parts) if parts else "无摘要"


def _fallback_summary(question: str, step_results: dict, error: str) -> str:
    """LLM 调用失败时的降级汇总（纯拼接，不调 LLM）"""
    lines = [
        f"## 查询结果汇总",
        f"> ⚠️ LLM 汇总失败: {error}",
        f"> 原始问题: {question}",
        "",
    ]
    for step_id, sr in sorted(step_results.items()):
        desc = sr.get("description", step_id)
        status = sr.get("status", "unknown")
        output = sr.get("output", "")
        if status == "success":
            lines.append(f"### {desc}")
            lines.append(str(output))
            lines.append("")
        elif status == "failed":
            lines.append(f"### {desc} ❌")
            lines.append(f"执行失败: {sr.get('error', '未知错误')}")
            lines.append("")
    return "\n".join(lines)


__all__ = [
    "reporter_node",
    "generate_final_answer",
    "_extract_sources_from_steps",
    "_extract_rag_references",
    "_is_step_successful",
    "_is_technical_error",
    "_user_step_label",
    "_format_step_outputs",
    "_fallback_summary",
]
