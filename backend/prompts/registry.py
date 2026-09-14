"""Prompt Registry — static metadata for all 38 prompts.

PromptSpec is a frozen dataclass; PROMPT_REGISTRY is the single source of truth.
security.input_guard is flagged code_controlled=True (never DB-editable).
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class VarSpec:
    name: str
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class PromptSpec:
    key: str
    name: str
    category: str
    risk_level: str  # critical / high / medium / low
    variables: tuple[VarSpec, ...] = ()
    code_controlled: bool = False
    required_substrings: tuple[str, ...] = ()
    default_file: str = ""
    agent: str = ""


R = VarSpec

PROMPT_REGISTRY: dict[str, PromptSpec] = {}


def _register(spec: PromptSpec) -> PromptSpec:
    PROMPT_REGISTRY[spec.key] = spec
    return spec


# ── Planner / Critique / Reporter ──────────────────────────────

_register(PromptSpec(
    key="planner.system",
    name="任务规划系统 Prompt",
    category="planner",
    risk_level="high",
    variables=(R("capabilities_schema"), R("cap_example")),
    default_file="planner_system.yaml",
))

_register(PromptSpec(
    key="planner.critique",
    name="计划审查 Prompt",
    category="planner",
    risk_level="high",
    variables=(R("capabilities_schema"),),
    default_file="planner_critique.yaml",
))

_register(PromptSpec(
    key="reporter.system",
    name="报告汇总 Prompt",
    category="reporter",
    risk_level="medium",
    default_file="reporter_system.yaml",
))

_register(PromptSpec(
    key="reporter.summary",
    name="报告快速总结",
    category="reporter",
    risk_level="low",
    variables=(R("data_summary"),),
    default_file="reporter_summary.yaml",
))

# ── RAG Pipeline ───────────────────────────────────────────────

_register(PromptSpec(
    key="rag.contextualize",
    name="查询重写 Prompt",
    category="rag",
    risk_level="medium",
    variables=(R("input"),),
    default_file="rag_contextualize.yaml",
))

_register(PromptSpec(
    key="rag.qa",
    name="RAG 问答生成 Prompt",
    category="rag",
    risk_level="medium",
    variables=(R("context"), R("input")),
    required_substrings=("<!--META",),
    default_file="rag_qa.yaml",
    agent="rag",
))

_register(PromptSpec(
    key="rag.document",
    name="证据格式化 Prompt",
    category="rag",
    risk_level="low",
    variables=(
        R("index"), R("query_label"), R("doc_label"), R("section_label"),
        R("chunk_label"), R("type_label"), R("domain_label"), R("page_content"),
    ),
    default_file="rag_document.yaml",
))

_register(PromptSpec(
    key="rag.multi_query",
    name="多查询扩展 Prompt",
    category="rag",
    risk_level="medium",
    variables=(R("count"), R("question")),
    default_file="rag_multi_query.yaml",
))

_register(PromptSpec(
    key="rag.guardrails.judge",
    name="LLM-as-Judge 忠实性",
    category="rag",
    risk_level="medium",
    variables=(R("context"), R("answer")),
    default_file="rag_guardrails_judge.yaml",
))

_register(PromptSpec(
    key="rag.guardrails.rewrite",
    name="事实修正 Prompt（已废弃）",
    category="rag",
    risk_level="medium",
    variables=(R("safe_chunk"), R("claim")),
    default_file="rag_guardrails_rewrite.yaml",
))

_register(PromptSpec(
    key="rag.evidence_gate.self_correction",
    name="自纠错查询改写",
    category="rag",
    risk_level="medium",
    variables=(R("reason"), R("question")),
    default_file="rag_evidence_gate_self_correction.yaml",
))

_register(PromptSpec(
    key="rag.preprocessing.chunk_batch",
    name="批量 Chunk 关键词提取",
    category="rag_preprocessing",
    risk_level="low",
    variables=(R("chunks_count"), R("top_k"), R("all_chunks")),
    default_file="rag_preprocessing_chunk_batch.yaml",
))

_register(PromptSpec(
    key="rag.preprocessing.chunk_single",
    name="单 Chunk 关键词提取",
    category="rag_preprocessing",
    risk_level="low",
    variables=(R("top_k"), R("safe_text")),
    default_file="rag_preprocessing_chunk_single.yaml",
))

_register(PromptSpec(
    key="rag.preprocessing.doc_ollama",
    name="文档级关键词提取（Ollama）",
    category="rag_preprocessing",
    risk_level="low",
    variables=(R("top_k"), R("safe_text")),
    default_file="rag_preprocessing_doc_ollama.yaml",
))

_register(PromptSpec(
    key="rag.preprocessing.doc_proxy",
    name="文档级关键词提取（云端）",
    category="rag_preprocessing",
    risk_level="low",
    variables=(R("top_k"), R("safe_text")),
    default_file="rag_preprocessing_doc_proxy.yaml",
))

_register(PromptSpec(
    key="rag.preprocessing.arbitration",
    name="文档类型仲裁",
    category="rag_preprocessing",
    risk_level="low",
    variables=(R("candidates"), R("text")),
    default_file="rag_preprocessing_arbitration.yaml",
))

_register(PromptSpec(
    key="rag.preprocessing.summary",
    name="文档摘要生成",
    category="rag_preprocessing",
    risk_level="low",
    variables=(R("safe_text"), R("max_length")),
    default_file="rag_preprocessing_summary.yaml",
))

_register(PromptSpec(
    key="rag.preprocessing.llm_enrichment",
    name="RAG 元数据提取",
    category="rag_preprocessing",
    risk_level="low",
    variables=(R("questions_field"), R("extra_schema"), R("safe_text"), R("chunks_block")),
    default_file="rag_preprocessing_llm_enrichment.yaml",
))

_register(PromptSpec(
    key="rag.indexing.doc_type",
    name="文档类型二次验证",
    category="rag_preprocessing",
    risk_level="low",
    variables=(R("full_text"),),
    default_file="rag_indexing_doc_type.yaml",
))

# ── Router / Orchestration ─────────────────────────────────────

_register(PromptSpec(
    key="router.llm",
    name="LLM 路由 Prompt",
    category="router",
    risk_level="high",
    variables=(R("query"),),
    default_file="router_llm.yaml",
))

_register(PromptSpec(
    key="selection_decision.review_pain",
    name="用户痛点分析",
    category="selection",
    risk_level="medium",
    default_file="selection_decision_review_pain.yaml",
))

_register(PromptSpec(
    key="selection_decision.differentiation",
    name="差异化分析",
    category="selection",
    risk_level="medium",
    default_file="selection_decision_differentiation.yaml",
))

# ── 市场调研（Skill 集成计划批次 2）────────────────────────────

_register(PromptSpec(
    key="market_research.analyzer",
    name="品类市场调研分组分析",
    category="selection",
    risk_level="medium",
    default_file="market_research_analyzer.yaml",
))

# ── SQL Agent ──────────────────────────────────────────────────

_register(PromptSpec(
    key="sql.generator",
    name="SQL 生成 Prompt",
    category="sql",
    risk_level="high",
    variables=(R("table_info"), R("question")),
    default_file="sql_generator.yaml",
    agent="sql",
))

_register(PromptSpec(
    key="sql.router",
    name="SQL 表路由 Prompt",
    category="sql",
    risk_level="high",
    variables=(R("table_list"), R("question")),
    default_file="sql_router.yaml",
))

# ── Memory ─────────────────────────────────────────────────────

_register(PromptSpec(
    key="memory.session.summary",
    name="会话摘要 Prompt",
    category="memory",
    risk_level="low",
    variables=(R("conversation"),),
    default_file="memory_session_summary.yaml",
))

_register(PromptSpec(
    key="memory.long_term.fact_extraction",
    name="事实提取 Prompt",
    category="memory",
    risk_level="low",
    variables=(R("conversation"),),
    default_file="memory_long_term_fact_extraction.yaml",
))

_register(PromptSpec(
    key="memory.trigger",
    name="记忆存储判断",
    category="memory",
    risk_level="low",
    variables=(R("content"),),
    default_file="memory_trigger.yaml",
))

# ── Security ───────────────────────────────────────────────────

_register(PromptSpec(
    key="security.input_guard",
    name="输入安全评估 Prompt",
    category="security",
    risk_level="critical",
    variables=(R("query"),),
    code_controlled=True,
    default_file="",
))

# ── Selection / Competitor / Business Report ───────────────────

_register(PromptSpec(
    key="selection.panel.vote",
    name="选品评审投票 Prompt",
    category="selection",
    risk_level="medium",
    variables=(R("role"), R("focus")),
    default_file="selection_panel_vote.yaml",
))

_register(PromptSpec(
    key="selection.recommender.reason",
    name="选品推荐理由",
    category="selection",
    risk_level="medium",
    variables=(
        R("title"), R("platform"), R("latest_price"), R("currency"),
        R("rating"), R("review_count"), R("highlights"),
        R("total"), R("breakdown"), R("notes"),
    ),
    default_file="selection_recommender_reason.yaml",
))

_register(PromptSpec(
    key="competitor.extractor",
    name="竞品数据抽取",
    category="competitor",
    risk_level="low",
    variables=(R("content"),),
    default_file="competitor_extractor.yaml",
))

_register(PromptSpec(
    key="business_report.polish",
    name="报告润色 Prompt",
    category="report",
    risk_level="medium",
    default_file="business_report_polish.yaml",
    agent="report",
))

# ── Customer Service Agent ─────────────────────────────────────

_register(PromptSpec(
    key="customer_service.system",
    name="客服系统 Prompt",
    category="customer_service",
    risk_level="high",
    default_file="customer_service_system.yaml",
    agent="customer_service",
))

_register(PromptSpec(
    key="customer_service.answer",
    name="客服回答生成 Prompt",
    category="customer_service",
    risk_level="high",
    variables=(R("context"), R("input")),
    default_file="customer_service_answer.yaml",
    agent="customer_service",
))

# ── Agent Capability ───────────────────────────────────────────

_register(PromptSpec(
    key="capability.inventory_analyzer",
    name="库存分析 Prompt",
    category="capability",
    risk_level="medium",
    variables=(R("rules"), R("inventory_data"), R("sales_data"), R("alert_level")),
    default_file="capability_inventory_analyzer.yaml",
))

# ── Evaluation ─────────────────────────────────────────────────

_register(PromptSpec(
    key="evaluation.judge.system",
    name="评估裁判系统 Prompt",
    category="evaluation",
    risk_level="low",
    default_file="",
))

_register(PromptSpec(
    key="evaluation.judge.user",
    name="评估裁判用户 Prompt",
    category="evaluation",
    risk_level="low",
    variables=(R("question"), R("rubric_lines"), R("actual_answer")),
    default_file="evaluation_judge_user.yaml",
))

# ── External Prompt Files ──────────────────────────────────────

_register(PromptSpec(
    key="skill.business_analysis",
    name="业务分析 Prompt",
    category="skill",
    risk_level="medium",
    variables=(R("columns"), R("sql_data"), R("knowledge")),
    default_file="skill_business_analysis.yaml",
))
