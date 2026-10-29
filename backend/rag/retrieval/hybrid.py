import json
import re
import threading

from backend.rag.retrieval_budget import (
    RetrievalDeadlineExceeded, await_retrieval, current_budget,
)

from backend.shared.logger import logger

def _record_request_retrieval_counts(
    *, vector_count: int, bm25_count: int, fused_count: int,
) -> None:
    """把原始召回计数放入当前请求，供 chain retrieval span 收口。"""
    try:
        from backend.rag.context import get_context

        get_context().meta["_retrieval_counts"] = {
            "vector_count": vector_count,
            "bm25_count": bm25_count,
            "fused_count": fused_count,
        }
    except Exception:  # noqa: BLE001 — 观测旁路失败不阻断检索
        logger.debug("写入检索计数失败", exc_info=True)

# 增强检索递归护栏：enhanced 路径内部空召回时会回调原始 hybrid_retrieve，
# 若此时再次进入增强分支会无限递归。按线程隔离，防止并发串扰。
_enhanced_inflight = threading.local()


def _fallback_id(doc) -> str:
    """当 chunk_id 缺失时，用 doc_id + chunk_index 生成回退标识。"""
    did = doc.metadata.get("doc_id", "?")
    ci = doc.metadata.get("chunk_index", 0)
    return f"{did}:{ci}"


def _decode_metadata_value(value):
    """将 PGVector 兼容层写入的 JSON 数组字符串还原为 Python 值。"""
    if isinstance(value, str) and value[:1] in ("[", "{"):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def _metadata_value_matches(actual, expected) -> bool:
    actual = _decode_metadata_value(actual)
    if isinstance(expected, dict):
        operator, operand = next(iter(expected.items()))
        if operator == "$eq":
            return _metadata_value_matches(actual, operand)
        if operator == "$ne":
            return not _metadata_value_matches(actual, operand)
        if operator == "$in":
            choices = set(operand or [])
            if isinstance(actual, list):
                return bool(set(actual) & choices)
            return actual in choices
        if operator == "$nin":
            choices = set(operand or [])
            if isinstance(actual, list):
                return not bool(set(actual) & choices)
            return actual not in choices
        raise ValueError(f"不支持的 metadata filter 操作符: {operator}")
    if isinstance(actual, list):
        return expected in actual
    return actual == expected


def _metadata_matches(metadata: dict, metadata_filter: dict | None) -> bool:
    """按向量库 where 语义判断一条 BM25 文档是否在过滤范围内。"""
    if not metadata_filter:
        return True
    if "$and" in metadata_filter:
        return all(_metadata_matches(metadata, part) for part in metadata_filter["$and"])
    if "$or" in metadata_filter:
        return any(_metadata_matches(metadata, part) for part in metadata_filter["$or"])
    return all(
        (
            key == "tenant_id" and expected == "default"
            and key not in metadata
        ) or _metadata_value_matches(metadata.get(key), expected)
        for key, expected in metadata_filter.items()
    )


def _filter_by_metadata(docs: list, metadata_filter: dict | None) -> list:
    """过滤 BM25 候选，兼容数组字段及 `$and`/`$or`。"""
    return [
        d for d in docs
        if _metadata_matches(d.metadata or {}, metadata_filter)
    ]


# ── 查询点名来源文件的候选收口（2026-10-29，UFQ-016~021 实证）──────────
#
# 「sku.csv 中 SKU 00012345 的价格」是用户显式圈定范围：其他文件的同标识符
# 行留在上下文里，生成端会在冲突值之间摇摆（枚举/拒答跨轮抖动）。点名文件
# 在候选中有命中时只保留该文件候选；零命中（文件名写错/未索引）保持原候选
# 不动，交由既有拒答规则处理。

_QUERY_NAMED_FILE_RE = re.compile(
    r"([0-9A-Za-z_\-\u4e00-\u9fff]{1,64}\.(?:csv|xlsx|xls|docx?|pptx?|md|txt|pdf|json))",
    re.IGNORECASE,
)

_DOC_SOURCE_FIELDS = ("source_file", "file_name", "canonical_id")


def _doc_source_file(doc) -> str:
    md = getattr(doc, "metadata", None) or {}
    for field in _DOC_SOURCE_FIELDS:
        value = md.get(field)
        if isinstance(value, str) and value.strip():
            # 只取 basename，兼容目录路径形式的登记值
            base = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
            return base.lower()
    return ""


def scope_by_named_files(docs: list, query: str) -> list:
    """查询点名了来源文件时，只保留该文件的候选（有命中才收口）。"""
    named = {m.group(1).lower() for m in _QUERY_NAMED_FILE_RE.finditer(query or "")}
    if not named:
        return docs
    kept = [d for d in docs if _doc_source_file(d) in named]
    if not kept:
        return docs
    return kept


import re

# =====================================================
# 三层查询路由分类器
# =====================================================
# vector_only:         简单 FAQ → 纯向量检索（跳过 BM25 + MultiQuery）
# hybrid:              普通查询 / 精确标识符 → Vector + BM25 + RRF
# hybrid_multi_query:  复杂 / 多意图 / 多跳 → Vector + BM25 + LLM 改写多路召回

# 精确标识符正则：检测到任一则 HYBRID（BM25 精确匹配有不可替代的价值）
# 治理 B（单一事实来源）：模式表由 query_router.py 持有（R4-P3 迁移），
# §7 查询类型路由的 exact_id 判定与本处 Tier 2 判定共用同一份表。
from backend.rag.retrieval.query_router import EXACT_IDENTIFIER_PATTERNS

# 复杂查询信号（Phase 4，2026-10-07 signal-score 分级，spec §21/§22）：
#   强信号：多意图/对比/多跳的确定性信号，单一命中即 HYBRID_MULTI_QUERY；
#   弱信号：「怎么/流程」类普通单意图常见词——单独命中只值 HYBRID 双路，
#           ≥2 个不同弱信号叠加才升 MQ（历史上单一弱信号即触发 3 变体
#           LLM 改写，「报销流程怎么走」也要付 ~1-2s 串行改写 + 3 路检索）。
# ⭐ 单一事实来源（治理 B）：multi_query.py 的兜底复杂度检测引用
#    COMPLEX_PATTERNS（= 强 ∪ 弱 聚合别名）；拆分仅发生在本文件，
#    勿在 multi_query.py 另行维护分表（P1-12 曾漂移，2026-09-13 回归修复）。
COMPLEX_STRONG_PATTERNS = [
    "对比", "比较", "区别", "差异", "不同", "优缺点", "利弊", "优劣",
    "分别", "各自", "两者", "哪个更",
]
COMPLEX_WEAK_PATTERNS = [
    "分析", "总结", "汇总", "概述",
    "全部", "所有",
    "关系", "影响", "作用", "意义",
    "以及", "同时", "另外", "还有", "并且",
    "怎么", "如何", "流程", "步骤", "方法", "原因", "为什么",
]
# 书面制度问法（2026-10-29 发货问排障）：「是怎么规定的」类措辞在问制度
# 条文，但知识库 FAQ 常为口语原文（「多久发货」），同义不同词时单查询
# 检索命不中（目标块进了 rerank 池仍被阈值截掉）。命中即升 MQ，靠 LLM
# 改写出口语变体。只收完整短语，不收「规定/怎么」单字散配——弱信号叠加
# 的长度门槛语义保持不变（§21 治理：短问不升级）。
FORMAL_POLICY_PATTERNS = (
    "是怎么规定的", "是怎么定义的", "如何规定的", "是怎样规定的",
)
# 多来源/多部门协作（2026-10-18）：这类问题的证据天然分散在多份文档，
# 单文档检索即使相关也只能覆盖部分事实（如"供应商准入"在供应商规范、
# "AQL/CE/FCC"在质检 SOP）。属于**结构性**多跳信号，命中即升 MQ。
# 只收明确的"跨来源"问法，不收"需要哪些材料"这类单文档事实问句。
MULTI_SOURCE_STRONG_PATTERNS = (
    "哪些部门", "哪些环节", "哪些岗位", "哪些角色",
    "涉及哪些", "由哪些", "分别由", "跨部门", "部门协作", "多方协作",
)
# 兼容别名（旧消费方 = multi_query 兜底检测）：强 ∪ 弱 ∪ 书面制度 ∪ 多来源
COMPLEX_PATTERNS = (
    COMPLEX_STRONG_PATTERNS + COMPLEX_WEAK_PATTERNS
    + list(FORMAL_POLICY_PATTERNS) + list(MULTI_SOURCE_STRONG_PATTERNS)
)

# 多意图连接词（长 query + 连接词 = §21 强信号「长 query + 多意图」）
_MULTI_INTENT_CONNECTIVES = ("以及", "同时", "另外", "还有", "并且", "分别")

# 多问号 / 多句子 → 多意图
_MULTI_QUESTION_THRESHOLD = 2

# ── 反问句剔除（2026-10-09 误触发修复）──────────────────────────────
# 缺陷：原先直接数问号（q.count("？")），把「一个论点 + 一个反问强调」
# 误判成多意图。实测「rag搜索不应该要30s？生产环境不能这么慢吧？」被判
# hybrid_multi_query → 触发 1 次 LLM 改写 + 3 路检索（白付 ~2.3s），
# 而它其实只有一个检索诉求。
# 修法：统计「真实疑问句」而非标点数量，反问句不计入。
#   1) 正反问 A不A（是不是/要不要/有没有…）→ 真问题，先排除；
#   2) 强反问标记（难道/岂/莫非）→ 反问；
#   3) 否定词 + 无疑问代词（不应该要30s？）→ 陈述式质疑，反问；
#   4) 否定词 + 吗/吧 结尾（不能这么慢吧？）→ 反问。
# 保守取向：判不准时按「真问题」处理（宁可多查一次，不可漏检多意图）。
_PROPER_QUESTION = re.compile(
    r"(是不是|要不要|能不能|有没有|可不可以|该不该|会不会|好不好)")
_RHETORICAL_MARKERS = re.compile(r"(难道|岂|莫非)")
_NEGATION = re.compile(r"(不|没|别|无|非)")
_QUESTION_WORDS = re.compile(
    r"(为什么|怎么|什么|哪|谁|多少|多久|几|是否|能否|如何|何时|何处)")
# 疑问分句判定标记 = 疑问代词 ∪ 正反问（A不A 是真问题，见 _is_rhetorical）
_QUESTION_MARKERS = re.compile(
    r"(为什么|怎么|什么|哪|谁|多少|多久|几|是否|能否|如何|何时|何处"
    r"|是不是|要不要|能不能|有没有|可不可以|该不该|会不会|好不好)")
# 「…吗/呢」结尾：无疑问代词但疑问语气明确的封闭式问句
# （「需要税号吗」）；否定+吗/吧 的反问形态已由 _is_rhetorical 先行排除
_QUESTION_PARTICLE = re.compile(r"(吗|呢)$")


def _is_rhetorical(sentence: str) -> bool:
    """判断单个分句是否为反问句（无疑问意图，不计入多意图信号）。"""
    s = (sentence or "").strip().rstrip("？?").strip()
    if not s:
        return False
    # 正反问 A不A 是真问题（「是不是有问题？」），必须优先排除
    if _PROPER_QUESTION.search(s):
        return False
    if _RHETORICAL_MARKERS.search(s):
        return True
    # 陈述式质疑：含否定但无疑问代词（「不应该要30s？」）
    if _NEGATION.search(s) and not _QUESTION_WORDS.search(s):
        return True
    # 否定 + 吗/吧 收尾（「生产环境不能这么慢吧？」）
    if _NEGATION.search(s) and re.search(r"(吗|吧)$", s):
        return True
    return False


def _count_real_questions(query: str) -> int:
    """统计真实疑问句数量（反问句不计）。

    与旧 q.count("？") 的差异就是本次修复点：把标点计数换成意图计数。
    2026-10-29 补第二处修复：分句必须**含疑问标记**（疑问代词或正反问）
    才计为真实疑问——按问号 split 后，剩余分句的疑问语气只能靠词面证明，
    否则「…？请给出处。」这类祈使后缀会被当成第二个疑问句，把单意图
    FAQ 问句误升 MQ（实测五条对照全部多付改写+3路检索+大池重排）。
    """
    parts = [p.strip() for p in re.split(r"[？?]", query) if p.strip()]
    return sum(
        1 for p in parts
        if not _is_rhetorical(p)
        and (_QUESTION_MARKERS.search(p)
             or _QUESTION_PARTICLE.search(p.rstrip("。．.　 ")))
    )

# Tier 1「简单 FAQ」的最大长度（去标点后字符数，D-Q2 修复 2026-10-05）：
# 此前兜底过宽——任何不含复杂词/标识符的问句（含专名长问「崇妙保圣坚牢塔
# 在哪里」）都落 vector_only 跳过 BM25；专名的向量语义弱、精确词匹配缺失，
#  造成系统性召回损失（实测 travel 库 REST ask 拒答率 70%，验收清单 Q2）。
# 收窄后：短问（≤8 字）维持 vector_only 纯向量；中长问句兜底走 hybrid
# 双路 RRF（BM25 为进程内索引，零外部调用成本，检索质量只增不减）。
_TIER1_MAX_CHARS = 8

# 去除标点/空白后计长（问句核心内容长度）
_TIER1_STRIP_RE = re.compile(r"[，。！？、；：「」《》\s!?;,.\[\]{}()\"'：:？—\-…]")


# ── 简单事实问句（R1-B 单一事实来源，2026-10-12）───────────────
# 为什么收归这里：multi_query._is_complex（分类器不可用时的兜底）与
# _classify_query_tier 曾各持一套顺序——兜底把「强信号」排在简单事实豁免
# 之前，导致「退款审核时间是多少？」一旦落到兜底就被"退款/审核"当业务
# 关键词判成复杂问句，白付 1 次 LLM 改写（~1.8s）+ 3 路检索。
# 现在两份清单与判定都从这里导出，禁止在 multi_query 另建分表。
SIMPLE_PREFIXES = ("什么是", "多少", "几点", "几号", "谁", "哪个", "哪里")

# 简单事实问句模式（2026-09-03 P1-7）：单一数值/时间点答案，改写只会放大重复检索。
SIMPLE_FACT_PATTERNS = (
    "是多少", "是多久", "多久", "几天", "多长时间", "什么时间", "什么时候", "多少钱", "多少个",
)


def is_simple_fact_query(query: str, *, sentence_parts: list | None = None) -> str:
    """判定单意图简单事实问句，命中返回原因，否则返回空串。

    边界：只对「单子句、单疑问、无多意图连接词」的问句生效。含对比/并列
    诉求的问句即使带「是多少」也不豁免——那正是 MultiQuery 该干的活。
    """
    q = (query or "").strip()
    if not q:
        return ""
    parts = sentence_parts
    if parts is None:
        parts = [x.strip() for x in re.split(r'[。！？；，、!?;,]', q) if x.strip()]
    # 多子句（≥2）天然是多诉求结构，不豁免
    if len(parts) >= 2:
        return ""
    if any(c in q for c in _MULTI_INTENT_CONNECTIVES):
        return ""
    if any(p in q for p in COMPLEX_STRONG_PATTERNS):
        return ""
    for pat in SIMPLE_FACT_PATTERNS:
        if pat in q:
            return f"简单事实问句({pat})"
    if q.startswith(SIMPLE_PREFIXES):
        return "简单事实问句"
    return ""


def _classify_query_tier(query: str) -> str:
    """三层查询分类。统一控制 BM25 开关 + MultiQuery 触发。

    优先级：Tier 3（复杂意图）→ Tier 2（精确标识符）→ Tier 1（兜底）
    复杂意图优先：含"分析""对比"等多意图查询即使包含产品型号，
    也需要 LLM 改写出多个子查询，而非仅靠 BM25 精排。

    Returns:
        "vector_only" | "hybrid" | "hybrid_multi_query"
    """
    from backend.config.rag import ADAPTIVE_RETRIEVAL_MODE

    if ADAPTIVE_RETRIEVAL_MODE in ("vector_only", "hybrid", "hybrid_multi_query"):
        return ADAPTIVE_RETRIEVAL_MODE

    q = query.strip()

    # ── Tier 3: 复杂 / 多意图 → HYBRID_MULTI_QUERY（最高优先级）──
    # 只计真实疑问句（反问剔除），见 _count_real_questions 的缺陷说明
    question_marks = _count_real_questions(q)
    # 过滤空字符串，避免 "问题？" split 后得到 ["问题", ""] 误判
    # 子句分隔含全角逗号/顿号（Phase 4）：「A是什么，怎么操作」是两个诉求，
    # 单靠句末标点会把它误判成单子句短问
    sentence_parts = [
        x.strip() for x in re.split(r'[。！？；，、!?;,]', q) if x.strip()
    ]
    # 收紧（2026-10-09）：由「问号≥2 **或** 子句≥3」改为
    # 「(真实问号≥2 且 子句≥2) **或** 子句≥3」。
    # 单纯两个问号不足以判定多意图（反驳/追问式表达常见），
    # 需同时具备多子句结构；子句≥3 仍单独成立（明确的并列诉求）。
    if (question_marks >= _MULTI_QUESTION_THRESHOLD
            and len(sentence_parts) >= 2) or len(sentence_parts) >= 3:
        return "hybrid_multi_query"

    # Phase 4 signal-score（§21）：
    #   强信号单一命中 → MQ；弱信号需 ≥2 个不同词叠加 → MQ；
    #   长 query + 多意图连接词 → MQ。
    #   弱信号叠加的判别：必须跨子句或问句足够长——「报销流程怎么走？」
    #   这类单子句短问里 流程+怎么 天然共现，仍算单一意图（任务书 §21 原例）。
    strong_hits = [p for p in COMPLEX_STRONG_PATTERNS if p in q]
    formal_hits = [p for p in FORMAL_POLICY_PATTERNS if p in q]
    multi_source_hits = [p for p in MULTI_SOURCE_STRONG_PATTERNS if p in q]
    weak_hits = [p for p in COMPLEX_WEAK_PATTERNS if p in q]
    # R1-B（P1-D 修正后）：简单事实豁免只作用于**弱信号叠加**分支，与基线
    # d1b9668 的 tier 语义保持一致（短问/Tier-2 标识符/弱信号单命中的既有
    # 行为不变）。强信号恒为 MQ：is_simple_fact_query 在强信号命中时必然
    # 返回空串，因此不会降级真正的多意图问句。
    if strong_hits or formal_hits or multi_source_hits:
        return "hybrid_multi_query"
    if len(weak_hits) >= 2 and (len(sentence_parts) >= 2 or len(q) > 16):
        if is_simple_fact_query(q, sentence_parts=sentence_parts):
            return "hybrid"
        return "hybrid_multi_query"
    if len(q) > 40 and any(c in q for c in _MULTI_INTENT_CONNECTIVES):
        return "hybrid_multi_query"

    # ── Tier 2: 精确标识符 → HYBRID ──
    # 错误码/订单号/SKU 等在知识库中通常有标准文档，
    # Vector + BM25 精排效果远好于纯向量检索
    for pattern in EXACT_IDENTIFIER_PATTERNS:
        if pattern.search(q):
            return "hybrid"

    # ── Tier 2.5: 引号/书名号专名 → HYBRID（D-Q2）──
    # 「」""《》是用户显式标记的精确名称（景点/书/条款），BM25 精确
    # 词匹配不可替代；这类 query 不满足 exact_id 正则但绝非「简单 FAQ」。
    if any(mark in q for mark in ("「", "《", '"', "《")):
        return "hybrid"

    # ── Tier 2 弱信号单命中 → HYBRID（Phase 4 新语义）──
    # 「报销流程怎么走？」类普通单意图：不再触发 3 变体 LLM 改写，
    # 但必须保住 BM25 双路（禁落 vector_only——2026-09-13 事故语义：
    # 流程类问题跳过 BM25 = 系统性召回损失）。
    if weak_hits:
        return "hybrid"

    # ── Tier 1: 简短 FAQ → VECTOR_ONLY；中长问句兜底 HYBRID（D-Q2）──
    compact = _TIER1_STRIP_RE.sub("", q)
    if len(compact) <= _TIER1_MAX_CHARS:
        return "vector_only"
    return "hybrid"


def _evaluate_retrieval_gate(merged: list, query: str):
    """Retrieval 阶段 Gate 评估，返回 GateDecision（总开关关闭时透传判定）。

    量纲修正（2026-09-03 事故修复）：注入时点文档通常只携带 rrf_score
    （RRF 量纲，上限 ≈0.033），与 VEC_MIN_SCORE（余弦相似度量纲，默认 0.2）
    不可比 —— 若文档无 rerank_score/similarity 等语义分数，跳过分数阈值
    检查，交由带真实分数的下游 chain 层 Gate 判定，避免必拒。
    """
    from backend.rag.evidence_gate import (
        evidence_gate_retrieval,
        gate_retrieval_passthrough,
        is_evidence_gate_enabled,
    )

    if not is_evidence_gate_enabled():
        return gate_retrieval_passthrough()

    qa_result = None
    try:
        from backend.rag.context import get_context
        qa_result = get_context().query_analysis
    except Exception:
        pass
    if qa_result is None:
        try:
            from backend.rag.retrieval.query_analyzer import QueryAnalyzer
            qa_result = QueryAnalyzer().analyze(query)
        except Exception as e:
            # 查询分析失败 → Gate 走无 query_analysis 兜底（软降级），留痕
            logger.debug(f"[hybrid_retrieve] QueryAnalyzer 分析失败: {e}", exc_info=True)

    from backend.config import VEC_MIN_SCORE

    has_semantic_score = any(
        d.metadata.get("rerank_score") is not None
        or d.metadata.get("similarity") is not None
        for d in merged
    )
    # P0-2（2026-10-19 治理改造）：DOC_TYPE_COVERAGE_REQUIRED 硬门退役——
    # 其输入 query_analysis.doc_types 100% 来自查询文本猜测，猜测即拒答
    # （TD-13「发票认证」被猜 security 假拒答同源）。相关性归检索+Rerank，
    # 不再按「猜测类型 vs 召回类型不匹配」二次拒答。
    # 实体覆盖校验（2026-10-25）在 rerank 前全集上执行并随 decision 注入：
    # 文档级标题 chunk（承载查询对文档的称呼实体）常被 Rerank 淘汰，
    # chain 层在 rerank 后子集上重算必误拒（实测「信息安全管理制度」问答
    # missing=['信息安全'] 挡住正确召回）。注入后 chain 凭 diagnostics 的
    # entity_check 键跳过重算；fail 拒答经 gate_passed=False 在 chain Gate 1 生效。
    from backend.config import GATE_ENTITY_CHECK_ENABLED

    return evidence_gate_retrieval(
        merged,
        query_analysis=qa_result,
        vec_min_score=VEC_MIN_SCORE if has_semantic_score else 0.0,
        require_doc_type_coverage=False,
        query=query,
        entity_check=GATE_ENTITY_CHECK_ENABLED,
    )


def _inject_gate_decision(docs: list, query: str) -> None:
    """在 rerank 前候选集上评估 Gate 并把 decision 注入每个 doc（2026-10-25）。

    注入全部候选而非仅首位：Rerank 可能淘汰任意候选（含 merged[0]），
    只写首位会让 decision 随淘汰丢失。chain 层 Gate 1 凭注入快路径消费
    rerank 前全集口径的实体覆盖校验结论，避免在 rerank 后子集上重算
    （子集覆盖面单调变窄，必制造误拒）。异常不拦截 docs（软降级留痕）。
    """
    if not docs:
        return
    try:
        decision = _evaluate_retrieval_gate(docs, query)
        _gate_payload = decision.to_metrics()
        for _d in docs:
            _d.metadata["__evidence_gate_decision__"] = _gate_payload
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[hybrid_retrieve] evidence_gate 评估异常: {e}")


def hybrid_retrieve(query, vector_retriever, bm25_retriever, k=5, doc_ids=None, rrf_k=60, metadata_filter=None,
                    expanded_queries: list[str] | None = None):
    """增强版混合检索 - 自动启用三路召回（Rule + Dense + Sparse）

    P2 优化集成点：
      - 从 enhanced_hybrid_retrieval 导入核心逻辑
      - 当 ADAPTIVE_THRESHOLD_ENABLED=true 时启用动态阈值
      - Rule-based retriever 仅对 FAQ/条款类查询激活
    4.1b: 返回前过滤 pending_review（near_dup 审核态）文档——软过滤，
    不动向量库 where（兼容无 review_status 字段的存量 chunk）。
    Batch 1A（2026-10-12）：实现收口到 retrieval.review_filter 公共模块，
    与 retrieve_knowledge / retrieve_documents 共用同一 fail-closed 出口；
    ask 为对客链路，缺 doc_id 的结果一律剔除（严格模式）。
    """
    docs = _hybrid_retrieve_impl(
        query, vector_retriever, bm25_retriever, k=k, doc_ids=doc_ids,
        rrf_k=rrf_k, metadata_filter=metadata_filter,
        expanded_queries=expanded_queries,
    )
    from backend.rag.retrieval.review_filter import apply_review_visibility
    return apply_review_visibility(
        docs, allow_missing_doc_id=False, context="hybrid")


# 审核可见性实现单一事实源（Batch 1A）：hybrid 侧只保留兼容别名，
# 行为（双层防线、fail-closed 与跨进程失效）见 review_filter 模块 docstring。
from backend.rag.retrieval.review_filter import (  # noqa: E402
    ReviewVisibilityUnavailableError,  # noqa: F401
    apply_review_visibility as _filter_review_blocked,
    get_review_blocked_doc_ids as _pending_review_doc_ids,
    invalidate_pending_review_cache,
)


def _hybrid_retrieve_impl(query, vector_retriever, bm25_retriever, k=5, doc_ids=None, rrf_k=60, metadata_filter=None,
                          expanded_queries: list[str] | None = None):
    """增强版混合检索实现（原 hybrid_retrieve 主体，4.1b 拆出便于结果过滤包装）。"""
    from backend.config.rag import ADAPTIVE_THRESHOLD_ENABLED, CONFIDENCE_AGGREGATOR_ENABLED
    
    # 尝试启用增强检索（如果配置开启且依赖可用；已在增强路径内则不再进入）
    if (ADAPTIVE_THRESHOLD_ENABLED and CONFIDENCE_AGGREGATOR_ENABLED
            and not getattr(_enhanced_inflight, "active", False)):
        try:
            logger.info(f"[hybrid_retrieve] Using ENHANCED multi-path retrieval for query='{query[:50]}...'")
            from backend.rag.retrieval.enhanced_hybrid_retrieval import (
                enhanced_hybrid_retrieve as enhanced_retrieve,
                ConfidenceAggregator,
            )
            
            confidence_aggregator = ConfidenceAggregator() if CONFIDENCE_AGGREGATOR_ENABLED else None
            _enhanced_inflight.active = True
            try:
                docs, meta = enhanced_retrieve(
                    query, vector_retriever, bm25_retriever,
                    k=k, doc_ids=doc_ids, rrf_k=rrf_k, metadata_filter=metadata_filter,
                    expanded_queries=expanded_queries,
                    confidence_aggregator=confidence_aggregator,
                )
            finally:
                _enhanced_inflight.active = False
            
            # 将 confidence 信息注入 metadata 供下游使用
            for i, doc in enumerate(docs[:3]):
                doc.metadata[f"enhanced_confidence_{i}"] = meta.get("confidence_score", 0)

            # Gate 注入（2026-10-25）：enhanced 分支此前直接 return，绕过
            # 尾部两处注入 → chain Gate 1 injected=None → 实体覆盖校验退化到
            # rerank 后子集重算（实测「信息安全管理制度」问答误拒）。
            _inject_gate_decision(docs, query)
            return docs
            
        except RetrievalDeadlineExceeded:
            raise
        except ImportError as e:
            logger.warning(f"[hybrid_retrieve] Enhanced retrieval import failed ({e}), fallback to original")
        except Exception as e:
            logger.warning(f"[hybrid_retrieve] Enhanced retrieval error ({e}), falling back to original")
    
    budget = current_budget()
    if budget is not None and budget.expired():
        raise RetrievalDeadlineExceeded("原始召回启动前共享预算耗尽")

    # Fallback: 原始 hybrid 逻辑
    from backend.observability.tracer import SpanName, trace_collector
    span = trace_collector.start_span("hybrid_retrieval", name=SpanName.HYBRID_RETRIEVAL, parent_id=None)

    # ── 三层查询路由：vector_only / hybrid / hybrid_multi_query ──
    query_tier = _classify_query_tier(query)
    logger.info(f"[hybrid_retrieve] query_tier={query_tier} for query='{query[:50]}...'")

    # ── §7 查询类型路由（R4-P3）：5 类判定 + RRF 加权策略 ──
    # vector_only 路径无融合，权重不消费，仅 query_type 进 trace/metrics
    from backend.rag.retrieval.query_router import route as route_query
    qroute = route_query(query)

    if query_tier == "vector_only":
        # Vector-only 路径：优先纯向量，避免关键词噪声稀释语义信号；
        # 但向量失败/空召回时降级 BM25 兜底（软降级，不伪装成『没有资料』）
        from backend.infra.thread_pools import submit_rag_task
        failures: dict[str, BaseException] = {}
        try:
            vector_docs = await_retrieval(submit_rag_task(
                "inner", vector_retriever.retrieve, query, k=k, doc_ids=doc_ids,
                metadata_filter=metadata_filter, expanded_queries=expanded_queries,
            ))
        except Exception as e:
            # embedding 模型与索引不一致 → 禁止降级 BM25（新向量查旧索引是噪声）
            from backend.rag.vectorstore.pgvector_store import (
                IndexEmbeddingMismatchError,
            )
            if isinstance(e, IndexEmbeddingMismatchError):
                logger.error(f"[hybrid_retrieve] {e}")
                raise
            logger.warning(
                f"[hybrid_retrieve] Vector-only 检索失败，降级 BM25: {e}",
                exc_info=True,
            )
            failures["vector"] = e
            vector_docs = []

        vector_docs = _filter_by_metadata(vector_docs, metadata_filter)
        if doc_ids:
            vector_docs = [d for d in vector_docs if d.metadata.get("doc_id") in doc_ids]

        bm25_docs: list = []
        if not vector_docs:
            # 向量空召回/失败 → BM25 兜底召回
            if bm25_retriever is None:
                # P2-3：冷启动空索引，BM25 路无候选
                bm25_docs = []
            else:
                try:
                    budget = current_budget()
                    if budget is not None and budget.expired():
                        raise RetrievalDeadlineExceeded("向量召回后共享预算耗尽")
                    bm25_docs = await_retrieval(submit_rag_task(
                        "inner", bm25_retriever.invoke, query,
                    ))
                    # P0：同主融合路，BM25 兜底也过滤独立 parent 命中
                    from backend.config.rag import RAG_FILTER_PARENT_RECALL
                    if RAG_FILTER_PARENT_RECALL:
                        bm25_docs = [d for d in bm25_docs
                                     if d.metadata.get("granularity") != "parent"]
                    bm25_docs = _filter_by_metadata(bm25_docs, metadata_filter)
                    if doc_ids:
                        bm25_docs = [d for d in bm25_docs
                                     if d.metadata.get("doc_id") in doc_ids]
                except Exception as e:
                    logger.warning(
                        f"[hybrid_retrieve] Vector-only 降级后 BM25 也失败: {e}",
                        exc_info=True,
                    )
                    failures["bm25"] = e
                    bm25_docs = []

        if len(failures) == 2:
            # 向量 + BM25 都失败 = 真系统失败，向上抛（与 hybrid 路径口径一致）
            raise RuntimeError(
                f"Vector 与 BM25 检索均失败: "
                f"vector={failures['vector']}, bm25={failures['bm25']}"
            ) from failures["vector"]

        merged = (vector_docs or bm25_docs)[:k]

        # 注入 rrf_score 占位（与 hybrid 路径 metadata 协议对齐）
        for rank, doc in enumerate(merged, start=1):
            doc.metadata["rrf_score"] = round(1 / (rrf_k + rank), 4)
            doc.metadata["retrieval_mode"] = "vector_only"

        if vector_docs:
            event_msg = f"Vector-only: {len(vector_docs)} docs (BM25 skipped)"
        else:
            event_msg = f"Vector-only 空召回/失败，BM25 fallback: {len(bm25_docs)} docs"
        trace_collector.add_event(span, "vector_only", "info", event_msg)

        metrics = {
            "query_tier": "vector_only",
            "query_type": qroute["query_type"],
            "vector_hits": len(vector_docs),
            "bm25_hits": len(bm25_docs),
            "merged_hits": len(merged),
            # D-4：统一四路召回计数键，供 Trace/验收消费；旧键保留兼容。
            "vector_count": len(vector_docs),
            "bm25_count": len(bm25_docs),
            "fused_count": len(merged),
            "rerank_count": len(merged),
        }
        if failures:
            # 单侧降级可观测：span metrics 标记 fallback 侧与原因
            metrics["fallback_side"] = ",".join(failures.keys())
            metrics["fallback_reason"] = "; ".join(
                f"{side}: {str(err)[:100]}" for side, err in failures.items()
            )
        _record_request_retrieval_counts(
            vector_count=len(vector_docs), bm25_count=len(bm25_docs),
            fused_count=len(merged),
        )
        trace_collector.end_span(span, metrics=metrics)

        if merged:
            _inject_gate_decision(merged, query)
        return merged

    # 财务 SQL 旁路检索：查询含财务指标 + 数值条件时并行执行
    sql_docs: list = []
    sql_bypass_used = False
    try:
        from backend.config import FINANCIAL_SQL_BYPASS_ENABLED
        if FINANCIAL_SQL_BYPASS_ENABLED:
            sql_docs = _financial_sql_bypass(query)
            if sql_docs:
                sql_bypass_used = True
                logger.info(f"[hybrid_retrieve] SQL 旁路检索命中 {len(sql_docs)} 条结构化数据")
    except Exception as e:
        logger.warning(f"[hybrid_retrieve] SQL 旁路检索失败，降级纯 RAG: {e}")

    # 并行执行：Vector 和 BM25 互不依赖（inner 池；Phase 3 统一经
    # submit_rag_task 出口埋 wait/exec 指标）
    from backend.infra.thread_pools import submit_rag_task
    failures: dict[str, BaseException] = {}
    vf = submit_rag_task("inner", vector_retriever.retrieve, query, k=k, doc_ids=doc_ids,
                         metadata_filter=metadata_filter, expanded_queries=expanded_queries)
    # P2-3（审查 2026-10-19）：空库冷启动 pipeline 允许 bm25=None（首批
    # 文档发布前为合法状态）。属性访问 None.invoke 会在 submit 行同步抛
    # AttributeError（不在任何异常处理内），故先判空——BM25 路无候选，
    # 向量路独立承接；首批发布后 BM25 自动可用。
    if bm25_retriever is None:
        failures_zero_bm25 = True
        bf = None
    else:
        failures_zero_bm25 = False
        bf = submit_rag_task("inner", bm25_retriever.invoke, query)
    try:
        vector_docs = await_retrieval(vf)
    except Exception as e:
        # 单侧失败 → 降级仅用另一侧（软降级），留痕；两侧都失败才上抛。
        # 例外：索引/运行时 embedding 不一致是门禁信号，必须上抛（2026-09-22）。
        from backend.rag.vectorstore.pgvector_store import (
            IndexEmbeddingMismatchError,
        )
        if isinstance(e, IndexEmbeddingMismatchError):
            logger.error(f"[hybrid_retrieve] {e}")
            raise
        logger.warning(
            f"[hybrid_retrieve] Vector 检索失败，降级仅用 BM25: {e}",
            exc_info=True,
        )
        failures["vector"] = e
        vector_docs = []
    try:
        bm25_docs = await_retrieval(bf) if bf is not None else []
    except Exception as e:
        logger.warning(
            f"[hybrid_retrieve] BM25 检索失败，降级仅用 Vector: {e}",
            exc_info=True,
        )
        failures["bm25"] = e
        bm25_docs = []

    if len(failures) == 2 or (
        failures_zero_bm25 and "vector" in failures
    ):
        # 两侧都失败（或 BM25 检索器缺位+向量失败）= 真系统失败，向上抛
        # （不伪装成『没有资料』的空召回）
        raise RuntimeError(
            f"Vector 与 BM25 检索均失败: "
            f"vector={failures.get('vector')}, bm25={failures.get('bm25', 'retriever=None(冷启动)')}"
        ) from failures.get("vector")

    if doc_ids:
        bm25_docs = [d for d in bm25_docs if d.metadata.get("doc_id") in doc_ids]

    # P0（2026-10-19 faq_only.docx 实证）：BM25 路同样过滤独立 parent 命中
    # （BM25 语料含虚拟 parent，问题列表与查询词面高度重合会挤占召回名额）；
    # parent 仅经 attach_parent_context 扩展通道提供上下文。与 CustomRetriever
    # 向量出口的过滤同源同开关。
    from backend.config.rag import RAG_FILTER_PARENT_RECALL
    if RAG_FILTER_PARENT_RECALL:
        bm25_docs = [d for d in bm25_docs
                     if d.metadata.get("granularity") != "parent"]

    # BM25 检索不接受 filter 参数，结果返回后手动按 metadata_filter 过滤，
    # 否则不同知识库的残留文档会混入、挤占 RRF 位置
    bm25_docs = _filter_by_metadata(bm25_docs, metadata_filter)

    bm25_docs = bm25_docs[:k*2]

    # ── RRF 融合（§7 查询类型路由加权：vector/bm25 各乘策略权重）──
    # qroute 权重中性（faq/disabled）时与加权前行为一致；
    # SQL 旁路自带精确匹配语义，不参与类型加权
    vw = qroute.get("vector_weight", 1.0)
    bw = qroute.get("bm25_weight", 1.0)
    rank_map: dict = {}

    for rank, doc in enumerate(vector_docs, start=1):
        cid = doc.metadata.get("chunk_id") or _fallback_id(doc)
        rank_map[cid] = rank_map.get(cid, 0) + vw / (rrf_k + rank)

    for rank, doc in enumerate(bm25_docs, start=1):
        cid = doc.metadata.get("chunk_id") or _fallback_id(doc)
        rank_map[cid] = rank_map.get(cid, 0) + bw / (rrf_k + rank)

    # SQL 旁路检索结果参与 RRF 融合（精确匹配，给高权重）
    for rank, doc in enumerate(sql_docs, start=1):
        cid = doc.metadata.get("chunk_id") or _fallback_id(doc)
        rank_map[cid] = rank_map.get(cid, 0) + 1 / (rrf_k + rank)

    # ── 标识符精确命中加权（2026-10-28）──
    # RRF 结构性偏差：查询含长数字/字母数字标识符（SKU/单号/型号）时，
    # 唯一被 BM25 精确命中的候选只能拿 1/(rrf_k+rank)，而"两路都出现的
    # 同模板相似行"（表格数据每行向量几乎相同）得分必然更高——精确行被
    # 系统性埋没（实测 SKU 000001 行 BM25 排第 1、重排打分 0.9884 却进
    # 不了 top5）。凡候选原文包含查询中的长标识符 token，给一个等效于
    # "最强单路第 1 名"的加性奖励，让它至少进入 rerank 视野由重排终裁。
    # 2026-10-29 加成基准对齐 enhanced 路径：路由加权（如 exact_id 的
    # bm25_weight=1.5）或 SQL 旁路存在时，固定 1/rrf_k 不足以站上最强
    # 单路第 1 名的分位，取实际权重上界作基准（中性路由下与旧值等价）。
    import os as _os
    import re as _re

    _id_tokens = {
        t.lower() for t in _re.findall(r"[0-9A-Za-z][0-9A-Za-z-]{3,}", query or "")
        if not t.isalpha() or len(t) >= 6
    }
    _bonus_hits: list[str] = []
    if _id_tokens:
        _EXACT_TOKEN_BONUS = max(vw, bw, 1.0) / rrf_k
        for doc in (*vector_docs, *bm25_docs, *sql_docs):
            cid = doc.metadata.get("chunk_id") or _fallback_id(doc)
            if cid not in rank_map:
                continue
            text = (doc.page_content or "").lower()
            if any(t in text for t in _id_tokens):
                rank_map[cid] += _EXACT_TOKEN_BONUS
                _bonus_hits.append(cid)

    sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)

    if _os.getenv("EVAL_TRIGGER") == "prompt_publish" and _id_tokens:
        logger.info(
            "[FusionDebug:hybrid] id_tokens=%s bonus=%s hits=%s top10=%s",
            sorted(_id_tokens), round(_EXACT_TOKEN_BONUS, 5), _bonus_hits[:10],
            [(c, round(s, 5)) for c, s in sorted_cids[:10]],
        )

    doc_dict = {doc.metadata.get("chunk_id") or _fallback_id(doc): doc for doc in vector_docs + bm25_docs + sql_docs}

    merged = []
    for cid, rrf_score in sorted_cids[:k]:
        doc = doc_dict[cid]
        doc.metadata["rrf_score"] = round(rrf_score, 4)
        doc.metadata["retrieval_mode"] = "hybrid"
        merged.append(doc)

    # 查询点名来源文件 → 只保留该文件候选（见 scope_by_named_files 注释）
    from backend.config.rag import RAG_QUERY_FILE_SCOPE

    if RAG_QUERY_FILE_SCOPE:
        _pre_scope = len(merged)
        merged = scope_by_named_files(merged, query)
        if len(merged) != _pre_scope:
            trace_collector.add_event(span, "named_file_scope", "info",
                f"{_pre_scope}→{len(merged)}（查询点名来源文件，仅保留该文件候选）",
                data={"kept": [d.metadata.get("chunk_id", "") for d in merged[:5]]})

    # ── Retrieval Debug event ──
    trace_collector.add_event(span, "rrf_fusion", "info",
        f"Vector:{len(vector_docs)} + BM25:{len(bm25_docs)} → RRF:{len(merged)}",
        data={
            "query_route": {"query_type": qroute["query_type"],
                            "signals": qroute.get("signals", []),
                            "vector_weight": vw, "bm25_weight": bw,
                            "enabled": qroute.get("enabled", True)},
            "vector_top3": [{"chunk_id": d.metadata.get("chunk_id", ""),
                             "score": round(rank_map.get(d.metadata.get("chunk_id") or _fallback_id(d), 0), 4),
                             "snippet": d.page_content[:120],
                             "source": d.metadata.get("source_file", ""),
                             "doc_type": d.metadata.get("doc_type", "")}
                            for d in vector_docs[:3]],
            "bm25_top3":  [{"chunk_id": d.metadata.get("chunk_id", ""),
                            "snippet": d.page_content[:120],
                            "source": d.metadata.get("source_file", ""),
                            "doc_type": d.metadata.get("doc_type", "")}
                           for d in bm25_docs[:3]],
            "fused_top5": [{"chunk_id": d.metadata.get("chunk_id", ""),
                            "rrf_score": round(rank_map.get(d.metadata.get("chunk_id") or _fallback_id(d), 0), 4),
                            "snippet": d.page_content[:120],
                            "source": d.metadata.get("source_file", ""),
                            "doc_type": d.metadata.get("doc_type", ""),
                            "keywords": d.metadata.get("chunk_keywords", "")}
                           for d in merged[:5]],
        })

    metrics = {"vector_hits": len(vector_docs),
               "bm25_hits": len(bm25_docs),
               "merged_hits": len(merged),
               # D-4：统一四路召回计数键，供 Trace/验收消费；旧键保留兼容。
               "vector_count": len(vector_docs),
               "bm25_count": len(bm25_docs),
               "fused_count": len(merged),
               "rerank_count": len(merged),
               "query_tier": query_tier,
               "query_type": qroute["query_type"]}
    if sql_bypass_used:
        metrics["sql_bypass_hits"] = len(sql_docs)
    if failures:
        # 单侧降级可观测：span metrics 标记 fallback 侧与原因（供 trace/前端定位）
        metrics["fallback_side"] = ",".join(failures.keys())
        metrics["fallback_reason"] = "; ".join(
            f"{k}: {str(v)[:100]}" for k, v in failures.items()
        )
    _record_request_retrieval_counts(
        vector_count=len(vector_docs), bm25_count=len(bm25_docs),
        fused_count=len(merged),
    )
    trace_collector.end_span(span, metrics=metrics)

    # ── Evidence Gate: Retrieval 阶段拒答判定 ────────────────
    # 接入各 doc 的 metadata 让下游 chain.py 能读取（Rerank 可能淘汰任意
    # 候选，只写首位会让 decision 随淘汰丢失）；若 docs 为空，下游
    # chain.py 直接再调一次 gate 处理 NO_EVIDENCE。
    if merged:
        _inject_gate_decision(merged, query)

    return merged


# ── 财务 SQL 旁路检索 ──────────────────────────────────


def _financial_sql_bypass(query: str) -> list:
    """财务 SQL 旁路检索：查询含财务指标 + 数值条件时，走 SQL 精确检索。

    流程：
      1. QueryAnalyzer 分析查询 → 提取 financial_metrics + numeric_conditions
      2. 仅当同时有指标和数值条件时才触发（避免对纯文本查询误走 SQL）
      3. 构造安全 SELECT SQL，查询已入库的结构化财务数据表
      4. SQL 结果转为 Document 对象，注入 RRF 融合

    失败软降级：SQL 执行失败返回空列表，不影响 Vector+BM25 检索。
    """
    from backend.shared.logger import logger

    try:
        from backend.rag.retrieval.query_analyzer import QueryAnalyzer
    except ImportError:
        return []

    parsed = QueryAnalyzer().analyze(query)
    # 仅当同时有财务指标和数值条件时才触发
    if not parsed.financial_metrics or not parsed.numeric_conditions:
        return []

    # 构造 SQL 查询
    sql = _build_financial_sql(parsed)
    if not sql:
        return []

    logger.info(f"[SQL_Bypass] 查询: {query[:80]}, SQL: {sql[:120]}")

    try:
        from backend.sql.sql_validator import sql_validator
        safe_sql, _, _ = sql_validator.validate(sql)
        from backend.sql.executor import execute_sql_struct
        from backend.sql.schema_loader import schema_loader
        result = execute_sql_struct(safe_sql, timeout=schema_loader.query_timeout)
        if result.status not in ("success", "no_data"):
            logger.warning(f"[SQL_Bypass] SQL 执行失败: {result.status} - {result.error}")
            return []
        return _sql_results_to_documents(result, parsed)
    except Exception as e:
        logger.warning(f"[SQL_Bypass] SQL 旁路检索失败: {e}")
        return []


def _build_financial_sql(parsed) -> str:
    """根据解析结果构造安全 SELECT SQL。

    策略：查询已入库的财务数据表，按 numeric_conditions 过滤。
    使用指标文本做 ILIKE 匹配列值，避免硬编码列名。
    表名由 schema_loader 动态发现，兑底用 stg_financial_data。
    """
    # 为每个 numeric_condition 构造 WHERE 子句
    where_clauses: list[str] = []
    for cond in parsed.numeric_conditions:
        metric = cond.get("metric_text", "")
        value = cond.get("value", 0)
        if not metric or not isinstance(value, (int, float)):
            continue
        # 指标文本可能在任意列中，用 ILIKE 跨列匹配
        # 简化：在已知财务表中按指标名做列值匹配
        where_clauses.append(
            f"EXISTS (SELECT 1 FROM unnest(string_to_array(::text, ',')) AS col_val "
            f"WHERE col_val ILIKE '%{metric}%')"
        )
        where_clauses.append(f"{value} IS NOT NULL")
        break  # 只用第一个条件，避免多条件冲突

    if not where_clauses:
        return ""

    # 动态查找含财务指标的表名，兑底用 stg_financial_data
    table_name = "stg_financial_data"
    try:
        from backend.sql.schema_loader import schema_loader
        known_tables = getattr(schema_loader, "tables", []) or []
        for t in known_tables:
            tname = str(getattr(t, "table_name", "")) or str(t)
            if "financial" in tname.lower() or "finance" in tname.lower():
                table_name = tname
                break
    except Exception:
        logger.debug("[P1-10] 财务表探测失败（降级默认表名）", exc_info=True)

    conditions = " AND ".join(where_clauses)
    sql = f"SELECT * FROM {table_name} WHERE {conditions} LIMIT 20"
    return sql


def _sql_results_to_documents(result, parsed) -> list:
    """将 SQL 查询结果转为 Document 列表。

    每行结果转成一个 Document，page_content 为 kv 格式文本，
    metadata 含 numeric_values 和来源标记。
    """
    import hashlib

    from langchain_core.documents import Document

    docs: list = []
    columns = result.columns or []
    rows = result.rows or []

    for ri, row in enumerate(rows):
        # 构建 kv 文本
        kv_parts: list[str] = ["[SQL财务数据]"]
        numeric_vals: dict[str, str] = {}
        for ci, col in enumerate(columns):
            if ci >= len(row):
                continue
            val = str(row[ci]) if row[ci] is not None else ""
            col_name = str(col).strip()
            if not col_name or not val:
                continue
            kv_parts.append(f"{col_name} {val}")
            # 尝试提取数值
            try:
                from backend.rag.preprocessing.financial_normalizer import normalize_financial_value
                norm = normalize_financial_value(val)
                if norm.is_numeric and norm.normalized is not None:
                    numeric_vals[col_name] = norm.normalized
            except Exception:
                logger.debug("[P1-10] 财务数值规范化失败", exc_info=True)

        text = "\n".join(kv_parts)
        chunk_id = hashlib.md5(f"sql:{ri}:{text[:100]}".encode()).hexdigest()[:12]
        meta = {
            "chunk_id": chunk_id,
            "doc_id": f"sql_bypass:{chunk_id}",
            "granularity": "leaf",
            "source_file": "sql_bypass",
            "chunk_type": "sql_result",
            "row_index": ri,
            "financial_metrics": parsed.financial_metrics,
            "chunk_tokens": 0,  # 由下游填充
        }
        if numeric_vals:
            meta["numeric_values"] = numeric_vals
        docs.append(Document(page_content=text, metadata=meta))

    return docs
