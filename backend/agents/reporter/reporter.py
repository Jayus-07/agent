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

import ast
import json
import re
from collections.abc import Mapping

from backend.infra.llm import llm
from langchain_core.messages import AIMessage
from backend.shared.logger import logger
from backend.agents.reporter.context_filter import filter_step_results
from backend.prompts.service import prompt_service

# 工具输出头部的机器标记（tools/rag.py 附带），与「### 参考文献」同属文本
# 协议：本模块负责在透传给用户 / 喂给汇总 LLM 前剥离；done 帧侧解析在
# events.make_done_event（读原始 step_results，不受此处剥离影响）
_RAG_META_RE = re.compile(r"<!--\s*RAGMETA\s*(\{.*?\})\s*-->\s*", re.DOTALL)


def strip_rag_meta(text: str) -> str:
    """剥离输出中的 <!--RAGMETA{...}--> 机器标记（无标记原样返回）。"""
    if text and "<!--RAGMETA" in text:
        return _RAG_META_RE.sub("", text).lstrip()
    return text


# =====================================================
# LangGraph 节点适配器
# =====================================================

def reporter_node(state: dict) -> dict:
    """LangGraph 节点适配器: state → generate_final_answer → {"final_answer": ...}"""
    from backend.orchestration.graph.sse_event_sink import emit_sse_progress

    emit_sse_progress(
        node="reporter", phase="answer_generation",
        message="正在整理最终答复",
    )
    # ── L1 弱命中追问（2026-09-19 拒答转追问）：clarify 模式下不执行任何
    # 汇总/LLM 逻辑；追问卡片事件已由 router 节点原始输出发出，这里只出
    # 一句如实告知的短文案（"_clarify" 不重复附带，防双卡片）
    if state.get("route_mode") == "clarify":
        from backend.orchestration.graph.clarify_content import (
            CLARIFY_STANDALONE_TEXT,
        )

        # CSInputGuard 的业务拦截也复用 clarify 短路，但需要保留其
        # 面向用户的安全提示；普通 L1 追问没有预置答案时才用通用文案。
        return {
            "final_answer": state.get("final_answer") or CLARIFY_STANDALONE_TEXT,
        }

    # ── 域引导（多域隔离 M2，2026-10-06）：handoff 模式同 clarify 短路 ──
    # 引导卡事件已由 router 节点原始输出（_handoff）发出，这里只出契约
    # 自带的引导话术；话术缺失时按域兜底，保证旧前端在正文里也有引导。
    if state.get("route_mode") == "handoff":
        from backend.orchestration.contracts.handoff import GUIDE_TEXTS

        payload = state.get("_handoff") or {}
        return {
            "final_answer": payload.get("text")
            or GUIDE_TEXTS.get(str(payload.get("target_domain") or ""), ""),
        }

    question = state.get("question", "")
    step_results = state.get("step_results", {})

    route_mode = str(state.get("route_mode") or state.get("executor_mode") or "")
    # Direct / Workflow 已有确定性执行结果；Reporter 仍保留在图中以维持
    # state 与出边契约，但只做规则渲染，不再调用会被 Runner 丢弃的 LLM。
    allow_llm = route_mode not in {"direct", "workflow"}
    answer = generate_final_answer(
        question=question,
        step_results=step_results,
        context_filter=allow_llm,
        allow_llm=allow_llm,
    )

    # ── 未答问题旁路登记（2026-10-03 知识运营闭环第一环）────────────
    # 与追问守卫解耦：即使防循环拦截了追问卡，知识缺口照样要留痕。
    _log_unanswered(answer, state)

    # ── L2 拒答兜底追问：所有步骤都无有效产出（RAG 拒答话术/空结果）且
    # 无技术性错误时，在节点原始输出附带 _clarify（events.py 据此发
    # clarification 事件）。拒答正文照常返回，不做静默替换。
    clarify = _refusal_clarify_marker(answer, state)
    if clarify is not None:
        return {
            "final_answer": answer,
            "clarification_request": clarify,
            "_clarify": clarify,
        }
    return {"final_answer": answer}


def _sql_empty_hit(state: dict) -> bool:
    """是否存在业务性空结果的 sql.query 步骤（查不到，非技术故障）。

    口径与 reporter 主流程一致：_is_step_successful 判伪 + 技术性错误
    （服务不可用）排除——那不是知识缺口，登记了会污染运营聚类。
    """
    for sr in (state.get("step_results") or {}).values():
        if sr.get("capability") != "sql.query":
            continue
        if sr.get("status") == "success" and sr.get("is_empty"):
            return True
        if _is_step_successful(sr):
            continue
        if _is_technical_error(sr):
            continue
        return True
    return False


def _has_actionable_failure(state: dict) -> bool:
    return any(
        _result_kind(sr) in {
            "permission_denied", "approval_required", "timeout",
            "unavailable", "rate_limited", "invalid_request", "failed",
        }
        for sr in (state.get("step_results") or {}).values()
    )


def _log_unanswered(answer: str, state: dict) -> None:
    """拒答 → 未答问题登记（旁路软失败；指标在 writer 入口即增）。

    只登记业务性拒答（「## 抱歉」模板且非技术故障）——服务不可用是
    故障不是知识缺口，归 trace/metrics，不进知识运营清单。
    """
    if not answer.startswith("## 抱歉") or "服务暂时不可用" in answer:
        return
    try:
        from backend.observability.unanswered import record_unanswered_question

        record_unanswered_question(
            state.get("question", ""),
            source="sql_empty" if _sql_empty_hit(state) else "rag_miss",
            department=str(state.get("department") or ""),
            kb_id=str(state.get("kb_id") or ""),
            detail={"route_mode": str(state.get("route_mode") or "")},
        )
    except Exception as e:  # noqa: BLE001 — 登记永不影响主流程
        logger.debug(f"[Reporter] 未答登记失败（软降级）: {e}")


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
            build_sql_empty_clarify,
            clarify_allowed,
            mark_clarified,
        )

        if not REFUSAL_CLARIFY_ENABLED:
            return None
        session_id = state.get("session_id", "")
        question = state.get("question", "")
        if not clarify_allowed(session_id, question):
            return None
        # SQL 空结果优先给定向槽位追问（时间范围/常用指标），其余按
        # 业务倾向给定向卡或通用导航
        if _sql_empty_hit(state):
            marker = build_sql_empty_clarify(question, executed=True)
        else:
            marker = build_refusal_clarify(
                question, state.get("domain_hint", ""))
        mark_clarified(
            session_id, question,
            options=marker.get("options"),
            source=marker.get("source", ""),
        )
        return marker
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
    allow_llm: bool = True,
) -> str:
    """
    生成最终 Markdown 回答（纯函数，无副作用）。

    Args:
        question: 原始用户问题
        step_results: {step_id: {capability, description, output, status, ...}}
        context_filter: 是否启用 Context Filter 过滤无关 RAG 结果
        allow_llm: 允许非流式最终汇总时调用 Reporter LLM

    Returns:
        最终 Markdown 格式回答（含参考文献）
    """
    # —— 全部失败 / 无有效输出：按 Tool Runtime 的结构化状态给安全话术 ——
    all_success = {
        sid: sr for sid, sr in step_results.items()
        if _is_step_successful(sr)
    }
    if not all_success:
        return _render_failure_summary(question, step_results)

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
            return _append_degraded_disclosure(
                strip_rag_meta(rag_output), step_results)

    # —— 快速路径：单步骤有实质输出时直接透传（省掉 LLM 总结 ~2s）——
    # 仅限字符串输出（RAG/报告类）；SQL/BusinessInsight 是结构化 dict，
    # 裸透传会变成 dict repr 展示给用户，必须走下方渲染或 LLM 路径
    if len(all_success) == 1:
        sole_sr = list(all_success.values())[0]
        sole_output = sole_sr.get("output", "")
        if isinstance(sole_output, str) and len(sole_output.strip()) > 5:
            logger.info("[Reporter] 单步骤有实质输出，直接透传（跳过 LLM 总结）")
            rendered = render_result_for_user(
                sole_sr.get("description", "查询结果"),
                sole_sr.get("capability", ""), sole_output,
            )
            return _append_degraded_disclosure(rendered, step_results)

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
        if not allow_llm:
            final = f"## 查询结果\n\n{structured}"
            if rag_references:
                final += rag_references
            return final
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
            if rag_references:
                final += rag_references
            return final

    if not allow_llm:
        return render_step_results_deterministically(step_results)

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

def _enum_text(value) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _result_kind(result: dict) -> str:
    """把已有 StepResult / ToolStatus 字段映射为用户展示语义。"""
    tool_status = _enum_text(result.get("tool_status"))
    error_type = _enum_text(result.get("error_type"))
    error_code = _enum_text(result.get("error_code"))
    lifecycle = _enum_text(result.get("lifecycle") or result.get("tool_lifecycle"))
    status = _enum_text(result.get("status"))
    cap = _enum_text(result.get("capability"))

    # 明确的安全拒绝优先于其他故障语义。
    if (tool_status == "unauthorized"
            or error_type in {"permission_denied", "unauthorized", "auth_failed",
                              "table_scope", "row_security"}
            or error_code in {"permission_denied", "unauthorized"}):
        return "permission_denied"
    if error_code == "approval_required" or lifecycle in {
            "pending_approval", "pending_confirmation"}:
        return "approval_required"
    if error_type == "permission":
        return "permission_denied"

    if (tool_status == "timeout" or error_type in {"timeout", "provider_timeout"}
            or error_code in {"timeout", "read_timeout", "connect_timeout"}):
        return "timeout"
    if (tool_status == "unavailable"
            or error_type in {"network", "service_unavailable", "unavailable",
                              "connection", "provider_error", "internal_error",
                              "exception", "router_error", "retry_exhausted"}
            or error_code in {"connect_error", "http_transport_error", "infra_error",
                              "circuit_open", "tool_busy"}):
        return "unavailable"
    if tool_status == "rate_limited" or error_type == "rate_limited" \
            or error_code == "rate_limited":
        return "rate_limited"
    if (tool_status == "invalid_request"
            or error_type in {"invalid_param", "validation_error", "invalid_request"}):
        return "invalid_request"

    if status == "partial":
        return "partial_success"
    if status == "success" and (
            result.get("degraded") is True
            or tool_status == "degraded"
            or bool(result.get("fallback_used"))):
        return "degraded"

    output = result.get("output")
    if status == "success" and cap == "sql.query" and (
            result.get("is_empty") is True
            or (isinstance(output, Mapping) and "rows" in output
                and not output.get("rows"))):
        return "no_data"
    if status == "success" and cap == "rag.search" \
            and isinstance(output, str) and _is_empty_output(output):
        return "no_evidence"
    if status == "skipped":
        return "skipped"
    if status == "failed" or status == "error":
        return "failed"
    if error_type:
        return "failed"
    return "success" if status == "success" else "failed"


_FAILURE_TEXT = {
    "permission_denied": "当前账号无权执行或访问该内容。",
    "approval_required": "该操作需要确认后才能执行，目前尚未完成。",
    "timeout": "查询超时，建议稍后重试。",
    "unavailable": "服务暂时不可用，请稍后重试。",
    "rate_limited": "请求受到频率限制，请稍后重试。",
    "invalid_request": "请求参数有误，请检查后重试。",
    "failed": "本次操作未能完成，建议稍后重试。",
    "skipped": "该步骤未执行，因此没有可展示的结果。",
    "no_evidence": "知识库没有足够可靠的依据回答该问题。",
    "no_data": "查询成功，但没有符合条件的数据（0 行）。",
    "degraded": "已使用降级结果，信息可能不完整。",
    "partial_success": "部分步骤已完成，结果可能不完整。",
}


def _failure_text(result: dict) -> str:
    kind = _result_kind(result)
    if kind == "permission_denied" and result.get("capability") == "sql.query":
        return "当前账号无权执行该查询或访问相关数据，请联系管理员申请相应的数据访问权限后重试。"
    if (kind == "approval_required"
            and isinstance(result.get("output"), str)
            and result.get("output").strip()):
        return result["output"].strip()[:300]
    return _FAILURE_TEXT.get(kind, _FAILURE_TEXT["failed"])


def _is_technical_error(error_or_result) -> bool:
    """只根据 ToolStatus / error_type 判故障，不猜测异常文本。"""
    if not isinstance(error_or_result, Mapping):
        return False
    return _result_kind(dict(error_or_result)) in {
        "timeout", "unavailable", "rate_limited", "invalid_request", "failed",
    }


def _render_failure_summary(question: str, step_results: dict) -> str:
    kinds = [_result_kind(sr) for sr in step_results.values()]
    if kinds and all(kind == "no_evidence" for kind in kinds):
        return (
            "## 抱歉\n\n"
            "知识库没有足够可靠的依据回答该问题。可以补充关键词或提供相关资料。"
        )

    rows = []
    for sr in step_results.values():
        kind = _result_kind(sr)
        label = _user_step_label(sr)
        rows.append(f"- {label}：{_failure_text(sr)}")
        error = str(sr.get("error") or "")
        if error:
            logger.warning("[Reporter] step 执行未完成: %s", error[:200])
    if not rows:
        return "## 查询未完成\n\n本轮没有可展示的结果，请调整问题后重试。"
    sql_permission_denied = (
        kinds
        and all(kind == "permission_denied" for kind in kinds)
        and all(sr.get("capability") == "sql.query" for sr in step_results.values())
    )
    if sql_permission_denied:
        heading = "## 权限不足"
    elif all(kind == "no_evidence" for kind in kinds if kind):
        heading = "## 抱歉"
    else:
        heading = "## 查询未完成"
    return heading + "\n\n" + "\n".join(rows)


def _is_step_successful(result: dict) -> bool:
    """依据步骤状态判断是否有可用结果；DEGRADED 仍保留成功数据。"""
    if result.get("capability") == "workflow":
        return result.get("status") in {"success", "partial"}
    if result.get("status") != "success":
        return False
    kind = _result_kind(result)
    if kind in {"permission_denied", "approval_required", "timeout", "unavailable",
                "rate_limited", "invalid_request", "failed", "no_evidence"}:
        return False
    if kind == "no_data":
        return True
    if result.get("error_type"):
        return False
    output = result.get("output")
    if output is None:
        return False
    if isinstance(output, str):
        if len(output.strip()) <= 5 or _is_empty_output(output):
            return False
    return True


_MAP_FIELD_LABELS = {
    "address": "地址", "formatted_address": "详细地址", "name": "名称",
    "title": "标题", "province": "省份", "city": "城市", "district": "区县",
    "lat": "纬度", "lng": "经度", "location": "坐标", "count": "结果数",
    "pois": "地点", "suggestions": "候选地点", "merchants": "商家",
    "distance": "距离", "distance_m": "距离（米）", "duration": "用时",
    "duration_min": "用时（分钟）", "from": "起点", "to": "终点",
    "route": "路线", "routes": "路线", "mode": "出行方式",
    "current": "当前天气", "days": "天气预报", "hours": "逐小时天气",
    "weather": "天气", "temperature": "温度", "condition": "天气状况",
    "humidity": "湿度", "wind": "风况", "description": "说明",
    "note": "说明", "text": "结果", "summary": "摘要", "data": "结果",
}
_INTERNAL_RESULT_KEYS = frozenset({
    "context_compacted", "type", "status", "error", "error_code", "raw",
    "debug", "traceback", "provider", "endpoint", "artifact_id",
    "original_tokens", "compacted", "sql", "sql_text", "tool", "step_id",
})


def _is_compacted_preview(output) -> bool:
    return (isinstance(output, Mapping)
            and output.get("context_compacted") is True
            and output.get("type") == "tool_result_preview")


def _render_map_data(value, depth: int = 0) -> str:
    if depth > 4:
        return ""
    if isinstance(value, Mapping):
        lines = []
        for key, item in value.items():
            key_text = str(key)
            if key_text in _INTERNAL_RESULT_KEYS:
                continue
            label = _MAP_FIELD_LABELS.get(key_text)
            if not label or item is None or item == "":
                continue
            if isinstance(item, Mapping):
                nested = _render_map_data(item, depth + 1)
                if nested:
                    lines.append(f"**{label}：**\n{nested}")
            elif isinstance(item, list):
                rendered = []
                for child in item[:20]:
                    if isinstance(child, Mapping):
                        child_text = _render_map_data(child, depth + 1)
                        if child_text:
                            rendered.append(f"- {child_text.replace(chr(10), chr(10) + '  ')}")
                    elif isinstance(child, (str, int, float)):
                        rendered.append(f"- {child}")
                if rendered:
                    lines.append(f"**{label}：**\n" + "\n".join(rendered))
            elif isinstance(item, (str, int, float, bool)):
                lines.append(f"- {label}：{item}")
        return "\n".join(lines)
    return ""


def _safe_text_output(output: str):
    text = strip_rag_meta(output or "").strip()
    if not text:
        return ""
    if text.startswith(("{", "[", "(")):
        parsed = None
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                parsed = None
        if isinstance(parsed, (Mapping, list, tuple)):
            return parsed
        if text.startswith(("{", "[")):
            return None
    if "Traceback (most recent call last)" in text or "provider=" in text \
            or "endpoint=" in text:
        return None
    return text


def render_result_for_user(description: str, capability: str, output) -> str:
    """按已知业务结果类型确定性渲染；未知机器对象返回安全说明。"""
    label = description or _CAP_USER_LABELS.get(capability, "查询结果")
    if _is_compacted_preview(output):
        return (f"### {label}\n\n结果已压缩，目前无法可靠展示完整内容。"
                "请缩小查询范围后重试。")
    if isinstance(output, str):
        output = _safe_text_output(output)
        if output is None:
            return f"### {label}\n\n{_RESULT_UNAVAILABLE}"
        if isinstance(output, str):
            return output
    if isinstance(output, Mapping):
        if output.get("status") in {"success", "ok"} and "data" in output:
            return render_result_for_user(label, capability, output.get("data"))
        if isinstance(output.get("report_md"), str):
            return output["report_md"].strip()
        if "rows" in output and ("columns" in output or capability == "sql.query"):
            columns = output.get("columns") or []
            rows = output.get("rows") or []
            if not rows:
                return f"### {label}\n\n{_FAILURE_TEXT['no_data']}"
            if not columns and isinstance(rows[0], Mapping):
                columns = list(rows[0])
            return _render_table_section(label, columns, rows)
        if "summary" in output and any(
                key in output for key in ("risks", "suggestions", "confidence")):
            return _render_insight_section(label, dict(output))
        if capability == "map.lookup":
            rendered = _render_map_data(output)
            if rendered:
                return f"### {label}\n\n{rendered}"
        for key in ("answer", "text", "message"):
            if isinstance(output.get(key), str) and output.get(key).strip():
                return strip_rag_meta(output[key]).strip()
        return f"### {label}\n\n{_RESULT_UNAVAILABLE}"
    if isinstance(output, (list, tuple)) and all(
            isinstance(item, (str, int, float)) for item in output):
        return "\n".join(f"- {item}" for item in output)
    if isinstance(output, (int, float)):
        return str(output)
    return f"### {label}\n\n{_RESULT_UNAVAILABLE}"


_RESULT_UNAVAILABLE = (
    "已取得结果，但当前格式无法可靠展示。请缩小查询范围或稍后重试。"
)


def _append_degraded_disclosure(answer: str, step_results: dict) -> str:
    degraded = [sr for sr in step_results.values()
                if _result_kind(sr) in {"degraded", "partial_success"}]
    if not degraded:
        return answer
    if _FAILURE_TEXT["degraded"] in answer:
        return answer
    notes = sorted({_FAILURE_TEXT[_result_kind(sr)] for sr in degraded})
    return answer.rstrip() + "\n\n> ⚠️ " + "；".join(notes)


def render_step_results_deterministically(step_results: dict) -> str:
    """Runner 兜底与 Direct/Workflow 共用的纯规则安全渲染。"""
    sections = []
    for step_id, sr in sorted((step_results or {}).items()):
        kind = _result_kind(sr)
        label = _user_step_label(sr)
        status = _enum_text(sr.get("status"))
        if kind in {"permission_denied", "approval_required", "timeout", "unavailable",
                    "rate_limited", "invalid_request", "failed", "skipped",
                    "no_evidence"}:
            sections.append(f"- {label}：{_failure_text(sr)}")
            continue
        output = sr.get("output")
        rendered = render_result_for_user(label, sr.get("capability", ""), output)
        if rendered:
            sections.append(rendered)
        if kind in {"degraded", "partial_success"}:
            sections.append(f"> ⚠️ {_FAILURE_TEXT[kind]}")
        elif status not in {"success", "partial"}:
            sections.append(f"- {label}：{_FAILURE_TEXT['failed']}")
    if not sections:
        return "## 查询未完成\n\n本轮没有可展示的结果，请调整问题后重试。"
    results = list((step_results or {}).values())
    sql_permission_denied = (
        results
        and all(_result_kind(sr) == "permission_denied" for sr in results)
        and all(sr.get("capability") == "sql.query" for sr in results)
    )
    heading = (
        "## 权限不足"
        if sql_permission_denied
        else "## 查询结果"
    )
    return heading + "\n\n" + "\n\n---\n\n".join(sections)


def _is_step_successful(result: dict) -> bool:
    """检查步骤是否真正成功（结构化判断）"""
    # workflow executor 直接产出最终答案（含 partial 降级续行），始终视为
    # 有效产出——partial 由 workflow 自身的披露行/尾注交代；若在此判伪，
    # workflow partial 会落入「## 抱歉」模板并误发追问卡（与已产出的
    # partial 结果自相矛盾），还会误登记未答问题。
    if result.get("capability") == "workflow":
        return True
    if result.get("status") != "success":
        return False
    if result.get("is_empty"):
        return False
    if result.get("error_type"):
        return False
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
            output = render_result_for_user(
                _user_step_label(sr), capability, sr.get("output"))
            if strip_references and capability == "rag.search":
                for marker in ["\n\n---\n\n### 参考文献", "\n\n---\n\n### 参考来源"]:
                    idx = output.find(marker)
                    if idx != -1:
                        output = output[:idx] + "\n\n*(参考文献已移至报告末尾)*"
                        break
            if len(output) > 3000:
                output = output[:3000] + "\n\n*(输出过长，已截断)*"
            suffix = (f"\n状态: 已降级，信息可能不完整"
                      if _result_kind(sr) == "degraded" else "")
            parts.append(f"{header}\n状态: ✅ 成功\n\n{output}{suffix}\n")
        elif status == "failed":
            parts.append(f"{header}\n状态: ❌ 未完成\n{_failure_text(sr)}\n")
        elif status == "skipped":
            parts.append(f"{header}\n状态: ⏭️ 已跳过\n{_failure_text(sr)}\n")
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
        description = _user_step_label(sr)

        if status == "success" and isinstance(output, Mapping):
            section_index = len(sections)
            # SQLResult → 表格（0 行也渲染说明，不再静默丢弃）
            if "rows" in output and capability == "sql.query":
                columns = output.get("columns", [])
                rows = output.get("rows", [])
                if rows:
                    if not columns and isinstance(rows[0], Mapping):
                        columns = list(rows[0])
                    section = _render_table_section(description, columns, rows)
                else:
                    section = (
                        f"### {description}\n\n"
                        f"{_FAILURE_TEXT['no_data']}"
                    )
                sections.append(section)
            # BusinessInsight → 风险+建议
            elif "summary" in output and any(
                    key in output for key in ("risks", "suggestions", "confidence")):
                section = _render_insight_section(description, output)
                sections.append(section)
            else:
                rendered = render_result_for_user(description, capability, output)
                if rendered:
                    sections.append(rendered)
            if (len(sections) > section_index
                    and _result_kind(sr) in {"degraded", "partial_success"}):
                sections[-1] += f"\n\n> ⚠️ {_FAILURE_TEXT[_result_kind(sr)]}"
            continue

        # 非成功 / 空话术步骤：显式交代（原实现完全隐身）
        if status != "success":
            label = _user_step_label(sr)
            sections.append(
                f"### {label}\n\n"
                f"- {_failure_text(sr)}"
            )
        elif isinstance(output, str) and _is_empty_output(output):
            label = _user_step_label(sr)
            sections.append(
                f"### {label}\n\n"
                f"- {_FAILURE_TEXT[_result_kind(sr)]}"
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
        f"查询成功，共 {len(rows)} 行。",
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
    if isinstance(confidence, (int, float)) and 0 <= confidence <= 1:
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
    """LLM 失败时回退到安全确定性渲染；异常细节只留在日志与 Trace。"""
    return render_step_results_deterministically(step_results)


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
    "render_result_for_user",
    "render_step_results_deterministically",
]
