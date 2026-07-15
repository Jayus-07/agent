"""
reporter.py — 最终 Markdown 回答生成

纯内容生成层:
  - 汇总 step_results（不关心它们怎么来的）
  - Context Filter 过滤无关 RAG 结果
  - LLM 生成最终 Markdown 回答
  - 引用/参考文献提取

不依赖:
  - LangGraph / multi_agent 调度框架
  - FastAPI / SSE
  - 数据库 / 向量库

可以被 API route 直接调用，也可以被 multi_agent 的 reporter_node 包装后调用。
"""

from backend.llm.llm_factory import llm
from backend.utils.logger import logger
from backend.response.context_filter import filter_step_results


REPORTER_SYSTEM = """你是专业的分析报告汇总专家。基于以下步骤执行结果，生成一份完整的最终回答。

## 严格规则
1. 只使用下方 step_results 中已存在的数据
2. 禁止编造任何不存在于 step_results 中的数字、日期、金额、百分比、事实陈述
3. 如果某个步骤失败或被跳过，在报告中简要标注，但不影响其他内容
4. 使用 Markdown 格式输出
5. **保留 RAG 检索结果中的引用编号**，如 [1]、[2]、[3]，不要删除或改写这些引用标记
6. 如果资料中有具体数字、日期、名称，必须原样保留，不得泛化或省略
7. 如果某个步骤的输出包含"已自动过滤"或"_filtered"标记，**不要向用户展示技术性评分**。
   - 你只需要告知用户：该信息来源因相关性不足未被采用。
   - 如果所有步骤均被过滤或无结果，直接回复"未找到相关信息"。

## 输出结构建议
- 先给一个简短的总览/摘要
- 然后按逻辑分节展开（不要简单罗列 step1、step2）
- 如果有数据表格，保持原有格式
- 如果有报告，将其嵌入到合适位置
- 最后可加简短总结"""


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
        failed_info = []
        for sid, sr in step_results.items():
            err = sr.get("error", "") or str(sr.get("output", ""))[:60]
            desc = sr.get("description", sid)
            failed_info.append(f"- {desc}: {err}" if err else f"- {desc}")
        detail = "\n".join(failed_info) if failed_info else "所有步骤均未产生有效结果"
        logger.info(f"[Reporter] 无有效输出，返回降级提示")
        return f"## 抱歉\n\n未能找到与「{question[:60]}」相关的信息。\n\n{detail}\n\n建议换个关键词或查阅其他资料。"

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

def _is_step_successful(result: dict) -> bool:
    """检查步骤是否真正成功（结构化判断）"""
    if result.get("status") != "success":
        return False
    if result.get("is_empty"):
        return False
    if result.get("error_type"):
        return False
    output = str(result.get("output", ""))
    if len(output.strip()) <= 20:
        return False
    return True


def _extract_rag_references(step_results: dict) -> str:
    """从 search_knowledge 步骤的输出中提取参考文献部分"""
    import re
    from backend.response.context_filter import parse_sources_from_text

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
    from backend.response.context_filter import parse_sources_from_text

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
