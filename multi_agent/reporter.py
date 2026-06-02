"""
reporter.py — Reporter 节点

职责:
  - 汇总所有 step_results
  - 生成最终 Markdown 回答
  - 禁止编造 step_results 中不存在的数据

不调用工具，不访问数据库，只看 step_results。
"""

from llm.llm_factory import llm
from utils.logger import logger

# =====================================================
# Reporter Prompt
# =====================================================

REPORTER_SYSTEM = """你是专业的分析报告汇总专家。基于以下步骤执行结果，生成一份完整的最终回答。

## 严格规则
1. 只使用下方 step_results 中已存在的数据
2. 禁止编造任何不存在于 step_results 中的数字、日期、金额、百分比、事实陈述
3. 如果某个步骤失败或被跳过，在报告中简要标注，但不影响其他内容
4. 使用 Markdown 格式输出

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
    """
    question = state.get("question", "")
    step_results = state.get("step_results", {})

    # 构建步骤输出摘要
    outputs_text = _format_step_outputs(step_results)

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

def _format_step_outputs(step_results: dict[str, dict]) -> str:
    """将 step_results 格式化为 Reporter 可读的文本"""
    parts = []

    for step_id, sr in sorted(step_results.items()):
        status = sr.get("status", "unknown")
        description = sr.get("description", step_id)
        capability = sr.get("capability", "")

        header = f"### 步骤 {step_id}: {description}"
        if status == "success":
            output = sr.get("output", "")
            # 截断过长输出（保留足够上下文）
            if len(str(output)) > 3000:
                output = str(output)[:3000] + "\n\n*(输出过长，已截断)*"
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
