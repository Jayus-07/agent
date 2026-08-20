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
from backend.shared.logger import logger
from backend.agents.reporter.context_filter import filter_step_results
from backend.prompts.reporter import REPORTER_SYSTEM


# =====================================================
# LangGraph 节点适配器
# =====================================================

def reporter_node(state: dict) -> dict:
    """LangGraph 节点适配器: state → generate_final_answer → {"final_answer": ...}"""
    question = state.get("question", "")
    step_results = state.get("step_results", {})

    answer = generate_final_answer(
        question=question,
        step_results=step_results,
        context_filter=True,
    )
    return {"final_answer": answer}


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

    # Context Filter
    if context_filter:
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
            # LLM 只写一句话执行摘要（<50 tokens）
            data_summary = _build_data_summary(step_results)
            resp = llm.invoke(
                f"""根据以下数据一句话总结业务状况（不超过50字）:

{data_summary}

直接输出总结，不要格式:"""
            )
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
        resp = llm.invoke([
            ("system", REPORTER_SYSTEM),
            ("human", f"## 用户问题\n{question}\n\n## 步骤执行结果\n{outputs_text}\n\n请生成最终报告:"),
        ])
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
    return True


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
    """
    sections = []
    for step_id, sr in sorted(step_results.items()):
        if sr.get("status") != "success":
            continue
        output = sr.get("output")
        if not isinstance(output, dict):
            continue

        capability = sr.get("capability", "")
        description = sr.get("description", step_id)

        # SQLResult → 表格
        if "columns" in output and "rows" in output and capability == "sql.query":
            columns = output.get("columns", [])
            rows = output.get("rows", [])
            if columns and rows:
                section = _render_table_section(description, columns, rows)
                sections.append(section)

        # BusinessInsight → 风险+建议
        elif "summary" in output and "risks" in output:
            section = _render_insight_section(description, output)
            sections.append(section)

    # 只有同时有表格和洞察时才走结构化路径
    if len(sections) >= 2:
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
    "REPORTER_SYSTEM",
    "_extract_sources_from_steps",
    "_extract_rag_references",
    "_is_step_successful",
    "_is_technical_error",
    "_user_step_label",
    "_format_step_outputs",
    "_fallback_summary",
]
