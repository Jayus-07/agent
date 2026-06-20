"""
reporter.py — Reporter 节点

职责:
  - 汇总所有 step_results
  - Context Filter: 过滤与问题无关的 RAG 检索结果
  - 生成最终 Markdown 回答
  - 禁止编造 step_results 中不存在的数据

不调用工具，不访问数据库，只看 step_results。
"""

from llm.llm_factory import llm
from utils.logger import logger

# Context Filter: RAG 输出与问题的最低相关度阈值
_CONTEXT_RELEVANCE_THRESHOLD = 0.35

# =====================================================
# Reporter Prompt
# =====================================================

REPORTER_SYSTEM = """你是专业的分析报告汇总专家。基于以下步骤执行结果，生成一份完整的最终回答。

## 严格规则
1. 只使用下方 step_results 中已存在的数据
2. 禁止编造任何不存在于 step_results 中的数字、日期、金额、百分比、事实陈述
3. 如果某个步骤失败或被跳过，在报告中简要标注，但不影响其他内容
4. 使用 Markdown 格式输出
5. **保留 RAG 检索结果中的引用编号**，如 [1]、[2]、[3]，不要删除或改写这些引用标记
6. 如果资料中有具体数字、日期、名称，必须原样保留，不得泛化或省略
7. 如果某个步骤的输出包含“已自动过滤”或“_filtered”标记，**不要向用户展示技术性评分（如 score=0.092）**。
   - 你只需要告知用户：该信息来源因相关性不足未被采用。
   - 如果所有步骤均被过滤或无结果，直接回复“未找到相关信息”，并建议用户检查关键词或提供更多上下文。
   - 过滤提示中的折叠原始内容（<details>标签）在最终回答中应视情况移除，或仅保留核心结论（如“资料未提及”）。
   
## 输出结构建议
- 先给一个简短的总览/摘要
- 然后按逻辑分节展开（不要简单罗列 step1、step2）
- 如果有数据表格，保持原有格式
- 如果有报告，将其嵌入到合适位置
- 最后可加简短总结"""


# =====================================================
# Reporter 节点
# =====================================================

def reporter_node(state: dict) -> dict:
    """
    汇总所有 step_results，生成最终回答。

    输入: state.question + state.step_results
    输出: {"final_answer": "..."}

    优化: 当只有 RAG 步骤成功且无其他步骤时，直接透传 RAG 原始输出
          避免 LLM 二次汇总丢失引用标注或编造内容。
    """
    question = state.get("question", "")
    step_results = state.get("step_results", {})

    # —— 全部失败 / 无有效输出：直接返回，不让 LLM 编造 ——
    all_success = {
        sid: sr for sid, sr in step_results.items()
        if sr.get("status") == "success" and sr.get("output")
        and len(str(sr.get("output", "")).strip()) > 20
        and "系统资源紧张" not in str(sr.get("output", ""))
    }
    if not all_success:
        failed_info = []
        for sid, sr in step_results.items():
            err = sr.get("error", "") or str(sr.get("output", ""))[:60]
            desc = sr.get("description", sid)
            failed_info.append(f"- {desc}: {err}" if err else f"- {desc}")
        detail = "\n".join(failed_info) if failed_info else "所有步骤均未产生有效结果"
        logger.info(f"[Reporter] 无有效输出，返回降级提示")
        return {"final_answer": f"## 抱歉\n\n未能找到与「{question[:60]}」相关的信息。\n\n{detail}\n\n建议换个关键词或查阅其他资料。"}

    # Context Filter: 过滤与问题无关的 RAG 检索结果
    step_results = _filter_step_results(step_results, question)

    # —— 快速路径：RAG 有结果且其他步骤无实质输出时，直接透传 ——
    rag_steps = {
        sid: sr for sid, sr in step_results.items()
        if sr.get("capability") == "search_knowledge" and sr.get("status") == "success"
        and sr.get("output") and len(str(sr.get("output", ""))) > 50
    }
    # 其他步骤有"实质输出"（非空、非错误、非纯元数据）
    other_meaningful = [
        sid for sid, sr in step_results.items()
        if sr.get("capability") != "search_knowledge"
        and sr.get("output")
        and len(str(sr.get("output", "")).strip()) > 30
        and "无结果" not in str(sr.get("output", ""))
        and "未找到" not in str(sr.get("output", ""))
    ]
    if rag_steps and not other_meaningful:
        rag_output = list(rag_steps.values())[0].get("output", "")
        if rag_output:
            logger.info("[Reporter] RAG 有实质输出且其他步骤无，直接透传原始输出（保留引用标注）")
            return {"final_answer": rag_output}

    # 从 RAG 步骤中提取参考文献（先行保存，LLM 汇总后再追加）
    rag_references = _extract_rag_references(step_results)

    # 构建步骤输出摘要（已剥离参考文献部分，避免 LLM 重复)
    outputs_text = _format_step_outputs(step_results, strip_references=True)

    if not outputs_text:
        logger.warning("[Reporter] 无可用的 step 输出")
        return {"final_answer": "## 抱歉\n\n未能生成报告。所有步骤均未产生有效输出。请检查数据源是否正常。"}

    logger.info(f"[Reporter] 汇总 {len(step_results)} 个步骤结果...")

    try:
        resp = llm.invoke([
            ("system", REPORTER_SYSTEM),
            ("human", f"## 用户问题\n{question}\n\n## 步骤执行结果\n{outputs_text}\n\n请生成最终报告:"),
        ])
        final = resp.content.strip()

        # 将 RAG 参考文献追加到最终输出
        if rag_references:
            final = final + rag_references

        logger.info(f"[Reporter] 最终报告: {len(final)} 字符")
        return {"final_answer": final}

    except Exception as e:
        logger.error(f"[Reporter] 汇总失败: {e}")
        # 降级：直接拼接所有输出
        fallback = _fallback_summary(question, step_results, str(e))
        return {"final_answer": fallback}


# =====================================================
# 辅助函数
# =====================================================

def _filter_step_results(step_results: dict, question: str) -> dict:
    """
    Context Filter: 过滤与问题无关的 RAG 检索结果。

    对每个 search_knowledge 步骤的输出，用 CrossEncoder 验证其与问题的相关性。
    低于阈值的输出替换为过滤标记，避免污染最终报告。
    """
    if not step_results:
        return step_results

    rag_steps = {
        sid: sr for sid, sr in step_results.items()
        if sr.get("capability") == "search_knowledge" and sr.get("status") == "success"
    }

    if not rag_steps:
        return step_results

    # 批量收集需要验证的 RAG 输出
    rag_ids = []
    rag_texts = []
    for sid, sr in rag_steps.items():
        output = str(sr.get("output", ""))
        if output and len(output) > 20:  # 跳过太短的输出
            rag_ids.append(sid)
            rag_texts.append(output[:800])

    if not rag_texts:
        return step_results

    # CrossEncoder 批量打分
    try:
        from retrieval.reranker import reranker as _ce
        from config import RERANK_TIMEOUT
        from utils.timeout import safe_call_with_timeout

        pairs = [(question, text) for text in rag_texts]
        scores = safe_call_with_timeout(
            _ce.predict,
            timeout=RERANK_TIMEOUT,
            default_value=None,
            error_message="Context Filter 超时",
            sentences=pairs,
        )
    except Exception as e:
        logger.warning(f"[ContextFilter] 模型加载失败，跳过过滤: {e}")
        return step_results

    if scores is None:
        logger.warning("[ContextFilter] 验证失败，跳过过滤")
        return step_results

    filtered = dict(step_results)
    filtered_count = 0

    for sid, score in zip(rag_ids, scores):
        if float(score) < _CONTEXT_RELEVANCE_THRESHOLD:
            sr = dict(filtered[sid])
            original_output = str(sr.get("output", ""))
            sr["output"] = (
                f"*(此条检索结果与问题「{question[:40]}...」相关性较低 (score={float(score):.3f})，"
                f"已自动过滤。如需参考，原始内容如下)*\n\n"
                f"<details>\n<summary>展开原始内容 ({len(original_output)} 字符)</summary>\n\n"
                f"{original_output[:500]}\n\n</details>"
            )
            sr["_filtered"] = True
            sr["_relevance_score"] = round(float(score), 4)
            filtered[sid] = sr
            filtered_count += 1
            logger.info(
                f"[ContextFilter] 过滤 step={sid} "
                f"description={sr.get('description', '')[:40]} "
                f"score={float(score):.3f} < {_CONTEXT_RELEVANCE_THRESHOLD}"
            )

    if filtered_count > 0:
        logger.info(f"[ContextFilter] 共过滤 {filtered_count}/{len(rag_ids)} 条无关 RAG 结果")

    return filtered


def _extract_rag_references(step_results: dict) -> str:
    """
    从 search_knowledge 步骤的输出中提取参考文献部分。

    RAG 管道返回的答案格式为 "...正文...\\n\\n---\\n\\n### 参考文献\\n\\n1. **xxx**..."
    提取参考文献部分，用于追加到 Reporter 最终输出。
    """
    import re

    all_refs = []
    seen_files = set()

    for sid, sr in step_results.items():
        if sr.get("capability") != "search_knowledge":
            continue
        if sr.get("status") != "success":
            continue
        if sr.get("_filtered"):
            continue  # 已被 Context Filter 标记过滤的，跳过

        output = str(sr.get("output", ""))

        # 查找 "### 参考文献" 或 "### 参考来源" 标记
        for marker in ["### 参考文献", "### 参考来源"]:
            idx = output.find(marker)
            if idx != -1:
                ref_section = output[idx:]
                # 提取每条参考文献行，按文件名去重
                ref_entries = []
                for line in ref_section.split("\n"):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or stripped.startswith("---"):
                        ref_entries.append(line)
                        continue
                    # 检查是否是新条目 (如 "1. **filename**")
                    m = re.match(r'\d+\.\s*\*\*(.+?)\*\*', stripped)
                    if m:
                        fname = m.group(1)
                        if fname not in seen_files:
                            seen_files.add(fname)
                            ref_entries.append(line)
                    elif seen_files:  # 后续行（如类型标签）跟着上一个条目
                        ref_entries.append(line)

                if ref_entries:
                    all_refs.append("\n".join(ref_entries))
                break

    if not all_refs:
        return ""

    return "\n\n" + "\n\n".join(all_refs)


def _parse_sources_from_text(text: str) -> list[dict]:
    """
    从包含参考文献的文本中提取结构化来源。
    解析格式: "N. **filename** (type_label) — 相关度: 0.94"
    """
    import re

    type_label_map = {
        "简历": "resume", "项目文档": "project", "报告": "report",
        "操作手册": "manual", "制度规范": "policy",
    }

    seen = {}
    for marker in ["### 参考文献", "### 参考来源"]:
        idx = text.find(marker)
        if idx == -1:
            continue
        ref_section = text[idx:]
        for line in ref_section.split("\n"):
            m = re.match(r'\d+\.\s*\*\*(.+?)\*\*\s*(?:\((.+?)\))?\s*(?:.*?相关度:\s*([\d.]+))?', line)
            if m:
                fname = m.group(1).strip()
                label = (m.group(2) or "").strip()
                score = float(m.group(3)) if m.group(3) else None
                if fname and fname not in seen:
                    doc_type = type_label_map.get(label, "general")
                    seen[fname] = {
                        "filename": fname,
                        "doc_type": doc_type,
                        "type_label": label or doc_type,
                        "score": round(score, 2) if score is not None else None,
                    }
        break

    return sorted(seen.values(), key=lambda s: s["filename"])


def _extract_sources_from_steps(step_results: dict) -> list[dict]:
    """从 RAG step_results 中提取结构化来源（优先从 output 文本解析）"""
    for sr in step_results.values():
        if sr.get("capability") != "search_knowledge":
            continue
        if sr.get("status") != "success" or sr.get("_filtered"):
            continue
        output = str(sr.get("output", ""))
        sources = _parse_sources_from_text(output)
        if sources:
            return sources

    # Fallback: try final_answer key on any step
    for sr in step_results.values():
        output = str(sr.get("output", sr.get("final_answer", "")))
        sources = _parse_sources_from_text(output)
        if sources:
            return sources

    return []


def _format_step_outputs(step_results: dict[str, dict], strip_references: bool = False) -> str:
    """将 step_results 格式化为 Reporter 可读的文本"""
    parts = []

    for step_id, sr in sorted(step_results.items()):
        status = sr.get("status", "unknown")
        description = sr.get("description", step_id)
        capability = sr.get("capability", "")

        header = f"### 步骤 {step_id}: {description}"
        if status == "success":
            output = str(sr.get("output", ""))

            # 剥离参考文献部分（由 _extract_rag_references 统一管理）
            if strip_references and capability == "search_knowledge":
                for marker in ["\n\n---\n\n### 参考文献", "\n\n---\n\n### 参考来源"]:
                    idx = output.find(marker)
                    if idx != -1:
                        output = output[:idx] + "\n\n*(参考文献已移至报告末尾)*"
                        break

            # 截断过长输出（保留足够上下文）
            if len(output) > 3000:
                output = output[:3000] + "\n\n*(输出过长，已截断)*"
            parts.append(f"{header}\n状态: ✅ 成功 ({capability})\n\n{output}\n")
        elif status == "failed":
            error = sr.get("error", "未知错误")
            parts.append(f"{header}\n状态: ❌ 失败 ({capability})\n错误: {error}\n")
        elif status == "skipped":
            error = sr.get("error", "")
            parts.append(f"{header}\n状态: ⏭️ 已跳过 ({capability})\n原因: {error}\n")
        else:
            parts.append(f"{header}\n状态: ⏳ {status}\n")

    return "\n".join(parts) if parts else ""


def _fallback_summary(question: str, step_results: dict, error: str) -> str:
    """LLM 调用失败时的降级汇总（纯拼接，不调 LLM）"""
    lines = [
        f"## 查询结果汇总",
        f"> ⚠️ LLM 汇总失败，以下为步骤原始输出拼接: {error}",
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
