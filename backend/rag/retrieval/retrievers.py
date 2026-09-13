"""
LangChain BaseRetriever 封装层
把现有的 CustomRetriever + BM25 + RRF + Reranker 包装为标准检索器接口
"""
from typing import List

from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from pydantic import Field

import json
from collections import Counter
from dataclasses import dataclass, field

from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun

from backend.rag.retrieval.hybrid import hybrid_retrieve
from backend.rag.preprocessing.entity import extract_person_names
from backend.rag.preprocessing.keyword import extract_chunk_keywords
from backend.config import (
    HYBRID_SEARCH_K,
    ADAPTIVE_CLUSTER_THRESHOLD,
    ADAPTIVE_MAX_CLUSTER_DOCS,
)
from backend.shared.logger import logger


# =====================================================
# 关键词重叠评分
# =====================================================

def _score_by_keyword_overlap(question: str, docs: list, fallback_k: int = 3, query_kw: set | None = None) -> list:
    """关键词软重排：命中文档按 overlap 降序排前面，未命中文档保留原始 embedding 顺序。

    不再硬过滤（丢弃 overlap=0 的文档），避免回归。
    增加子串模糊匹配：query kw 与 doc kw 存在包含关系时计 0.5 分。
    """
    if query_kw is None:
        query_kw = set(extract_chunk_keywords(question, top_k=10))
    if not query_kw:
        return docs

    scored = []
    for doc in docs:
        raw = doc.metadata.get("doc_keywords", "")
        if isinstance(raw, list):
            doc_kw = set(raw)
        elif raw:
            try:
                doc_kw = set(json.loads(raw) if raw.startswith("[") else raw.split(", "))
            except (json.JSONDecodeError, TypeError):
                doc_kw = set()
        else:
            doc_kw = set()
        exact = len(query_kw & doc_kw)
        fuzzy = 0.0
        if not exact:
            for qk in query_kw:
                for dk in doc_kw:
                    if len(qk) >= 2 and len(dk) >= 2 and (qk in dk or dk in qk):
                        fuzzy += 0.5
                        break
        score = exact + fuzzy
        scored.append((doc, score))

    matched = [(doc, s) for doc, s in scored if s > 0]
    unmatched = [doc for doc, s in scored if s == 0]
    matched.sort(key=lambda x: x[1], reverse=True)

    logger.info(f"关键词重排: query_kw={query_kw}, 命中 {len(matched)}/{len(docs)}")
    return [doc for doc, _ in matched] + unmatched


def _dedup_by_doc_id(docs: list) -> list:
    """按 doc_id 去重，保留首次出现（相似度最高）的条目。"""
    seen = set()
    result = []
    for doc in docs:
        did = doc.metadata.get("doc_id")
        if did and did in seen:
            continue
        if did:
            seen.add(did)
        result.append(doc)
    return result


def attach_parent_context(docs: List[Document], parent_lookup) -> List[Document]:
    """Parent-Child 上下文增强：检索命中的 leaf，拉取对应 parent 提供完整上下文。

    小 chunk（leaf）精确检索、大 chunk（parent）提供完整上下文。检索命中的
    leaf 带 parent_chunk_id，这里把不在结果中的 parent 拉进来，避免 LLM 只
    看到碎片化的 leaf。

    Args:
        docs: 检索返回的 chunk（leaf + parent 混合）
        parent_lookup: Callable[[list[str]], list[Document]]，按 chunk_id 列表返回 parent

    Returns:
        原 docs + 拉取到的 parent（按 chunk_id 去重）

    降级：parent_lookup 抛异常时返回原 docs，不丢检索结果。
    """
    if not docs:
        return list(docs)

    seen = {d.metadata.get("chunk_id") for d in docs}
    parent_ids = {
        d.metadata.get("parent_chunk_id")
        for d in docs
        if d.metadata.get("granularity") == "leaf"
        and d.metadata.get("parent_chunk_id")
        and d.metadata.get("parent_chunk_id") not in seen
    }
    if not parent_ids:
        return list(docs)

    try:
        parents = parent_lookup(list(parent_ids))
    except Exception as e:
        logger.warning(
            f"[Parent-Child] 拉取 parent 失败({type(e).__name__})，降级返回原结果: {e}"
        )
        return list(docs)

    added = [p for p in parents if p.metadata.get("chunk_id") not in seen]
    if added:
        logger.info(f"[Parent-Child] 拉取 {len(added)} 个 parent 上下文")
    return list(docs) + added


# =====================================================
# 请求内检索缓存（2026-09-03 P1-5）
# =====================================================

def _copy_docs(docs: list) -> list:
    """Document 浅拷贝：隔离 metadata，防止调用方改写污染缓存。"""
    return [
        Document(page_content=d.page_content, metadata=dict(d.metadata))
        for d in docs
    ]


def _retrieval_cache_hits() -> int:
    """当前请求上下文的检索缓存命中次数（可观测）。"""
    from backend.rag.context import get_context
    return get_context().retrieval_cache_hits


def _cache_key(query: str, metadata_filter: dict, k: int) -> str:
    """缓存键：(query, metadata_filter, k)；filter 用 JSON 序列化兼容嵌套/列表值。"""
    try:
        f = json.dumps(metadata_filter or {}, sort_keys=True, default=str)
    except Exception:
        f = str(metadata_filter)
    return f"{query}\x00{f}\x00{k}"


def _scope_kb_filter(metadata_filter: dict, allowed: set) -> dict:
    """把 filter 中的 kb 范围收敛到授权集合（主体已声明时）。

    - 无 kb 限定 / 完全在授权内 → 原样返回（保留精确 pushdown）
    - 部分/完全不在授权内 → 去掉 kb_id/$or 限定（交由 keep-set 后过滤收窄；
      不改写为 $in——手工后过滤是等值语义，$in 会误杀全部结果）
    """
    if not metadata_filter:
        return metadata_filter
    scoped: set = set()
    if "kb_id" in metadata_filter:
        v = metadata_filter["kb_id"]
        scoped = {v} if isinstance(v, str) else set(v)
    if "$or" in metadata_filter:
        for item in metadata_filter["$or"]:
            if isinstance(item, dict) and "kb_id" in item:
                v = item["kb_id"]
                scoped |= {v} if isinstance(v, str) else set(v)
    if not scoped or scoped <= allowed:
        return metadata_filter
    return {k: v for k, v in metadata_filter.items()
            if k not in ("kb_id", "$or")}


def _keep_docs_in_allowed_kbs(docs: list, allowed: set, span=None) -> list:
    """主体已声明时强制白名单：只保留授权库的文档（未知 kb 一律剔除）。

    与 _drop_excluded_kb_fallback_docs 的区别：这是无条件执行的主体授权
    （显式 kb 选择也受限——LLM 选库不可信，属性裁决），后者仅作用于
    跨库兜底路径的 test 库排除（未声明主体的旧行为）。
    """
    kept = [d for d in docs if d.metadata.get("kb_id") in allowed]
    dropped = len(docs) - len(kept)
    if dropped:
        logger.info(
            f"ChunkLevelRetriever: 主体授权剔除越界库文档 {dropped} 条 "
            f"(allowed={sorted(allowed)})"
        )
        if span is not None:
            from backend.observability.tracer import trace_collector
            trace_collector.add_event(span, "subject_scope_filtered", "info",
                f"主体授权剔除越界库文档: {dropped}",
                data={"allowed_kbs": sorted(allowed),
                      "dropped": dropped, "kept": len(kept)})
    return kept


def _drop_excluded_kb_fallback_docs(docs: list, span=None) -> list:
    """跨库兜底禁入剔除（2026-09-14）：kb 放宽后的召回剔除禁入库文档。

    f17 的"宁跨 KB 召回，不全量拒答"不适用于测试/评测库——虚构数据经兜底
    路径对客输出等同信息安全事故。名单由 KNOWLEDGE_BASES 的 audience 标签
    推导（单一来源），仅作用于 kb 放宽路径，显式 kb_id 检索不受影响；
    CROSS_KB_FALLBACK_EXCLUDE_TEST=false 一键回滚。
    """
    from backend.config.knowledge_base import cross_kb_fallback_excluded
    excluded = cross_kb_fallback_excluded()
    if not excluded or not docs:
        return docs
    kept = [d for d in docs
            if d.metadata.get("kb_id") not in excluded]
    dropped = len(docs) - len(kept)
    if dropped:
        logger.warning(
            f"ChunkLevelRetriever: 跨库兜底剔除禁入库文档 {dropped} 条 "
            f"(excluded={excluded})"
        )
        if span is not None:
            from backend.observability.tracer import trace_collector
            trace_collector.add_event(span, "cross_kb_fallback_excluded", "warning",
                f"跨库兜底剔除禁入库文档: {dropped}",
                data={"excluded_kbs": excluded,
                      "dropped": dropped, "kept": len(kept)})
    return kept


# =====================================================
# 检索阶段共享状态（staging context）
# =====================================================

@dataclass
class _Staging:
    """一次未命中缓存检索的跨阶段状态（ChunkLevelRetriever 专用）。

    检索链 9 层降级拆分前，十几个局部变量在单个 300 行函数里串状态；
    拆分后各 Stage 方法按固定顺序读写此对象。字段按阶段分组，
    语义与拆分前的局部变量一一对应——只动结构不动行为。
    """

    query: str
    span: object  # chunk_retrieval span（不可序列化，仅用于挂载 trace 事件）

    # ── Stage 0 产出（此后只读；0 匹配放宽会改写 metadata_filter/doc_ids）──
    metadata_filter: dict = field(default_factory=dict)
    subject_type: str = ""
    department: str = ""
    # 主体授权 keep-set；None=未声明主体（授权未启用，旧行为）
    authorized: set | None = None

    # ── Stage 1 产出 ──
    person_names: list = field(default_factory=list)
    doc_ids: list | None = None
    gate_info: dict | None = None  # Stage1 门控契约数据（治理 A：排除理由留痕）
    # 路径契约值: person_name / person_name_miss /
    # metadata_filter_with_doc_similarity / doc_similarity / keyword_filter /
    # domain_fallback / kb_fallback（空召回重试追加 +synonym_retry 后缀）
    stage1_path: str | None = None
    stage1_fallback_count: int = 0  # 累计退化次数（>=1 即认为走了 fallback）
    # kb 兜底放宽发生过 → 未声明主体时召回结果需剔除 test 库（旧行为；
    # 已声明主体走 authorized 白名单，更严格）
    cross_kb_fallback: bool = False

    # ── Stage 2 产出 ──
    docs: list = field(default_factory=list)
    seen: set = field(default_factory=set)  # 已收录 chunk_id（跨重试/扩展去重）
    expanded_queries: list | None = None
    effective_k: int = 0  # adaptive 扩展后的实际 k（调用方截断用）


# =====================================================
# Chunk-Level Retriever
# =====================================================

class ChunkLevelRetriever(BaseRetriever):
    """片段级检索器：Doc→Chunk 两阶段检索（MultiQuery 由外部 MultiQueryRetriever 处理）"""

    doc_db: object = Field(description="文档级 ChromaDB")
    vectordb: object = Field(description="片段级 ChromaDB")
    chunk_retriever: object = Field(description="CustomRetriever (vector)")
    bm25: object = Field(description="BM25 检索器")
    person_index: dict = Field(default_factory=dict, description="人名 → doc_ids 倒排索引")
    k: int = HYBRID_SEARCH_K

    class Config:
        arbitrary_types_allowed = True

    @staticmethod
    def _doc_name(meta: dict) -> str:
        """doc 级 metadata → 可读名称（Stage1 契约 trace 用）。"""
        return meta.get("source") or meta.get("source_file") or meta.get("doc_id") or "?"

    @staticmethod
    def _filter_docs_by_keywords(question: str, doc_results: list, fallback_k: int = 3) -> tuple:
        """Stage1 文档门控，返回 (doc_ids, gate_info)。

        gate_info 是阶段契约数据（2026-09-13 治理 A）：记录候选/关键词命中/
        相似度兜底/最终保留/被排除项及理由，经 trace event 暴露 —— 此前本阶段
        静默丢弃文档（相似度 Top-1 因 keywords 为空被踢）导致排查耗时数小时。
        """
        deduped = _dedup_by_doc_id(doc_results)
        query_kw = set(extract_chunk_keywords(question, top_k=10))
        reranked = _score_by_keyword_overlap(question, deduped, fallback_k, query_kw=query_kw)
        all_ids = [
            doc.metadata.get("doc_id")
            for doc in reranked
            if doc.metadata.get("doc_id")
        ]
        id2name = {
            d.metadata.get("doc_id"): ChunkLevelRetriever._doc_name(d.metadata)
            for d in deduped
            if d.metadata.get("doc_id")
        }
        if not query_kw:
            ids = list(dict.fromkeys(all_ids))
            return ids, {
                "reason": "no_query_keywords",
                "candidates": [id2name[i] for i in ids],
                "keyword_matched": [],
                "similarity_top": [],
                "kept": [id2name[i] for i in ids],
                "excluded": [],
            }
        similarity_top = [
            d.metadata.get("doc_id")
            for d in deduped[:fallback_k]
            if d.metadata.get("doc_id")
        ]
        matched_ids = []
        for doc in reranked:
            did = doc.metadata.get("doc_id")
            if not did:
                continue
            raw = doc.metadata.get("doc_keywords", "")
            if isinstance(raw, list):
                doc_kw = set(raw)
            elif raw:
                try:
                    doc_kw = set(json.loads(raw) if raw.startswith("[") else raw.split(", "))
                except (json.JSONDecodeError, TypeError):
                    doc_kw = set()
            else:
                doc_kw = set()
            if query_kw & doc_kw:
                matched_ids.append(did)
        unique_matched = list(dict.fromkeys(matched_ids))
        if unique_matched:
            # 相似度兜底并集：doc_keywords 缺失/为空的文档（关键词提取失败的
            # 历史文档、测试夹具）不应仅因 metadata 没有关键词而被 Stage1
            # 整体排除 —— 即使它是 doc 级相似度 Top-1（2026-09-13 golden
            # RC-080/086/095 回归）。并入 doc 相似度前 fallback_k 名保底；
            # 注意必须取 deduped（相似度序）而非 reranked（关键词重排序），
            # 否则兜底名额会被关键词命中文档占满，等于没兜底。
            ids = list(dict.fromkeys(unique_matched + similarity_top))
        else:
            ids = list(dict.fromkeys(all_ids))
        kept_set = set(ids)
        info = {
            "reason": "keyword_match_plus_similarity_top" if unique_matched else "keyword_fallback_all",
            "candidates": [id2name[i] for i in all_ids],
            "keyword_matched": [id2name[i] for i in unique_matched],
            "similarity_top": [id2name[i] for i in similarity_top],
            "kept": [id2name[i] for i in ids],
            "excluded": [
                {"doc": id2name[i], "reason": "no_keyword_match_and_below_similarity_top"}
                for i in all_ids if i not in kept_set
            ],
        }
        return ids, info

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List[Document]:
        """带请求内缓存的检索入口（P1-5）。

        同一请求内相同 (query, metadata_filter, k) 的重复调用直接命中缓存，
        避免 MultiQuery 变体 × 同义词扩展组合出的重复检索；缓存随
        RequestContext（contextvars）隔离，跨请求不共享。返回副本防止
        下游对 metadata 的改写（如 source_query）污染缓存。
        """
        from backend.rag.context import get_context

        ctx = get_context()
        key = _cache_key(query, ctx.metadata_filter, self.k)
        cached = ctx.retrieval_cache.get(key)
        if cached is not None:
            ctx.retrieval_cache_hits += 1
            logger.info(
                f"[ChunkLevelRetriever] 请求内缓存命中({ctx.retrieval_cache_hits}): {query[:30]}"
            )
            return _copy_docs(cached)

        docs = self._retrieve_uncached(query)
        ctx.retrieval_cache[key] = _copy_docs(docs)
        return docs

    def _retrieve_uncached(self, query: str) -> List[Document]:
        """带 span 生命周期保障的入口：异常/提前 return 都不会泄漏 span。

        旧实现两处提前 return（neighbor expansion / Stage2 空结果）漏调
        end_span，导致 trace 中出现 end_time='' 的假 success span。
        """
        from backend.observability.tracer import trace_collector, SpanName
        span = trace_collector.start_span("chunk_retrieval", name=SpanName.CHUNK_RETRIEVAL, parent_id=None)
        try:
            docs, metrics = self._retrieve_uncached_impl(query, span)
        except Exception:
            trace_collector.end_span(span, status="error")
            raise
        trace_collector.end_span(span, metrics=metrics)
        return docs

    def _retrieve_uncached_impl(self, query: str, span) -> "tuple[List[Document], dict]":
        """检索编排：按固定顺序执行各 Stage，阶段间状态经 _Staging 传递。

        Stage 划分只动结构不动行为；trace 事件名与 metrics 键（stage1_path
        取值、stage1_fallback_count、fallback: neighbor_expansion）是阶段契约
        （RC-086/095 事故排查的回放依据），名称与取值不可变。
        """
        st = _Staging(query=query, span=span)
        self._load_request_context(st)
        self._stage1_select_docs(st)
        self._emit_stage1_events(st)
        self._stage1_relax_on_zero_match(st)
        self._stage2_hybrid_retrieve(st)
        early = self._stage2_neighbor_fallback(st)
        if early is not None:
            return early
        self._stage2_postprocess(st)
        return st.docs[: st.effective_k], {
            "retrieved_chunks": len(st.docs),
            "stage1_path": st.stage1_path,
            "stage1_fallback_count": st.stage1_fallback_count,
        }

    def _load_request_context(self, st: "_Staging") -> None:
        """Stage 0：读取请求上下文 + 计算主体授权。

        输入: contextvars 中的 RequestContext（缺失 → 软降级为无 filter，留痕）
        输出: st.metadata_filter / st.subject_type / st.department /
              st.authorized（keep-set；有授权时 filter 的 kb 范围已收敛）
        """
        try:
            from backend.rag.context import get_context
            ctx = get_context()
            st.metadata_filter = ctx.metadata_filter
            # 身份从权威上下文组合借读（identity 缺失/异常均软降级为未声明主体）
            identity = getattr(ctx, "identity", None)
            st.subject_type = getattr(identity, "subject_type", "") or ""
            st.department = getattr(identity, "department", "") or ""
        except Exception as e:
            # request 上下文缺失 → 按无 filter 全量检索（软降级），留痕
            logger.debug(f"[ChunkLevelRetriever] 读取 request context 失败: {e}", exc_info=True)

        # ── 主体授权（属性驱动，确定性计算）──
        # st.authorized=None 表示未声明主体 → 授权未启用（旧行为）；已声明则
        # filter 中的 kb 范围先收敛到授权集合，后续召回再以 keep-set 兜底——
        # 显式 kb 选择（含 LLM 选库）同样受限，路由只提议、属性裁决。
        from backend.config.knowledge_base import authorized_kbs
        authorized = authorized_kbs(st.subject_type, st.department)
        if authorized is not None:
            st.authorized = set(authorized)
            st.metadata_filter = _scope_kb_filter(st.metadata_filter, st.authorized)

    def _authorized_doc_search(self, st: "_Staging", k: int = 15, flt: dict | None = None) -> list:
        """Doc 级检索 + 主体授权后过滤（st.authorized 为 None 时零行为变化）。"""
        docs = (self.doc_db.similarity_search(st.query, k=k, filter=flt)
                if flt else self.doc_db.similarity_search(st.query, k=k))
        if st.authorized is not None:
            docs = [d for d in docs if d.metadata.get("kb_id") in st.authorized]
        return docs

    def _stage1_select_docs(self, st: "_Staging") -> None:
        """Stage 1：Doc 级候选选择（人名索引 → filter/相似度/关键词门控）。

        输入: st.query / st.metadata_filter / st.authorized / self.person_index
        输出: st.person_names / st.doc_ids / st.gate_info /
              st.stage1_path / st.stage1_fallback_count
        🟢 V1.5 埋点：stage1_path 契约取值（4 个候选）
        person_name: 人名索引命中；doc_similarity: 退化到 doc 级向量检索；
        keyword_filter: 退化到关键词过滤；domain_fallback: 0 匹配业务域回退。
        """
        st.person_names = extract_person_names(st.query)
        if st.metadata_filter:
            # MetadataFilter has already determined the scope — use it directly
            known_persons = st.metadata_filter.get("person_names")
            if known_persons:
                p = known_persons
                if isinstance(p, list):
                    p = p[0]
                matched_ids = self.person_index.get(p, [])
                if matched_ids:
                    st.doc_ids = matched_ids
                    st.stage1_path = "person_name"
                else:
                    st.doc_ids = []
                    st.stage1_path = "person_name_miss"
            else:
                # 即使有 metadata_filter（如 kb_id）也要做 doc 级检索计算 doc_ids，
                # 否则 Stage2 hybrid_retrieve doc_ids=[] 不限 doc，rerank 输入被 KB 内噪声稀释
                # 导致高相关 doc 被挤掉（fix 2026-08-19 — RAG eval 基线从 72% 恢复）
                doc_results = self._authorized_doc_search(st, flt=st.metadata_filter)
                st.stage1_fallback_count += 1
                st.doc_ids, st.gate_info = self._filter_docs_by_keywords(st.query, doc_results)
                st.stage1_path = "metadata_filter_with_doc_similarity"
            logger.info(
                f"ChunkLevelRetriever Stage 1: metadata_filter={st.metadata_filter} "
                f"→ doc_ids={len(st.doc_ids)} matched, path={st.stage1_path}"
            )
        else:
            if st.person_names:
                person_name = st.person_names[0] if isinstance(st.person_names, list) else st.person_names
                matched_ids = self.person_index.get(person_name, [])
                if matched_ids:
                    st.doc_ids = matched_ids
                    st.stage1_path = "person_name"
                    logger.info(f"ChunkLevelRetriever: 人名匹配到 {len(st.doc_ids)} 个文档")
                else:
                    doc_results = self._authorized_doc_search(st)
                    st.stage1_fallback_count += 1
                    st.doc_ids, st.gate_info = self._filter_docs_by_keywords(st.query, doc_results)
                    st.stage1_path = "doc_similarity" if doc_results else "keyword_filter"
            else:
                doc_results = self._authorized_doc_search(st)
                st.stage1_fallback_count += 1
                if doc_results:
                    st.doc_ids, st.gate_info = self._filter_docs_by_keywords(st.query, doc_results)
                    st.stage1_path = "doc_similarity"
                else:
                    st.doc_ids, st.gate_info = self._filter_docs_by_keywords(st.query, doc_results)
                    st.stage1_path = "keyword_filter"

            if st.doc_ids:
                logger.info(f"ChunkLevelRetriever Stage 1: 召回 {len(st.doc_ids)} 个相关文档, path={st.stage1_path}")

    def _emit_stage1_events(self, st: "_Staging") -> None:
        """Stage 1 契约事件：doc_filter（路径/规模）+ stage1_doc_gate（门控明细）。

        2026-09-13 RC-086/095 事故：相似度 Top-1 文档因 keywords 为空被静默
        排除，无任何"谁被排除、为什么"的留痕，排查耗时数小时。本事件保证
        门控决策全程可回放。
        """
        from backend.observability.tracer import trace_collector
        # ── Doc Filter event ──
        trace_collector.add_event(st.span, "doc_filter", "info",
            f"Stage1: metadata={st.metadata_filter}, persons={st.person_names}, → {len(st.doc_ids or [])} docs, path={st.stage1_path}",
            data={"metadata_filter": st.metadata_filter,
                  "person_names": st.person_names,
                  "output_doc_count": len(st.doc_ids or []),
                  "stage1_path": st.stage1_path,
                  "stage1_fallback_count": st.stage1_fallback_count})

        # ── Stage1 门控契约（治理 A）：候选/命中/兜底/排除项及理由 ──
        if st.gate_info:
            excluded_names = [e["doc"] for e in st.gate_info["excluded"]]
            trace_collector.add_event(st.span, "stage1_doc_gate", "info",
                f"Stage1 门控: kept={len(st.gate_info['kept'])}, "
                f"excluded={len(st.gate_info['excluded'])}, reason={st.gate_info['reason']}",
                data=st.gate_info)
            if excluded_names:
                logger.info(
                    f"[Stage1 门控] 排除 {len(excluded_names)} 个文档: "
                    f"{excluded_names[:5]}{'...' if len(excluded_names) > 5 else ''} "
                    f"(理由: 无关键词命中且不在相似度 Top{3})"
                )

    def _stage1_relax_on_zero_match(self, st: "_Staging") -> None:
        """Stage 1 0 匹配兜底：顺序放宽 business_domain → kb_id。

        输入: st.metadata_filter / st.doc_ids（0 匹配时触发）
        输出: 改写 st.metadata_filter / st.doc_ids=None / st.stage1_path /
              st.stage1_fallback_count / st.cross_kb_fallback
        """
        if not (st.metadata_filter and not st.doc_ids):
            return
        # 🟢 2026-08-10 新增：Stage 1 0 匹配 fallback
        # 解决 metadata_filter 推 business_domain 不准时丢文档的问题
        # （如问"差评怎么处理" → customer，但售后流程文档标 order）
        # 2026-09-10 fix：旧实现 domain/kb 两级放宽是 if/else 二选一 —— 只要
        # business_domain 在 filter 中就只放宽它，剩余 kb_id（可能指向空 KB，
        # 如「退款」→ biz_order 但文档实际在 rag_test_kb）永远不会被放宽，
        # 兜底形同虚设 → 0 召回 → Evidence Gate 假拒答。
        # 改为顺序放宽：先放宽 business_domain，再探测 KB 是否有文档，
        # 空则连 kb_id 一起放宽（宁跨 KB 召回，不全量拒答）。
        from backend.observability.tracer import trace_collector
        fallback_filter = {k: v for k, v in st.metadata_filter.items() if k != "business_domain"}
        if fallback_filter != st.metadata_filter:
            logger.info(
                f"ChunkLevelRetriever: metadata_filter {st.metadata_filter} 0 匹配, "
                f"回退到放宽 business_domain 的检索"
            )
            st.metadata_filter = fallback_filter
            st.doc_ids = None  # 让 Stage 2 走完整向量检索
            st.stage1_path = "domain_fallback"
            st.stage1_fallback_count += 1
            # 探测放宽 domain 后 KB 是否仍有文档；空 KB → 继续放宽 kb_id
            if not self._filter_has_docs(fallback_filter):
                kb_relaxed = {
                    k: v for k, v in fallback_filter.items()
                    if k not in ("kb_id", "$or")
                }
                if kb_relaxed != fallback_filter:
                    logger.info(
                        f"ChunkLevelRetriever: 放宽 business_domain 后仍无文档 "
                        f"(filter={fallback_filter})，继续放宽 kb_id → {kb_relaxed}"
                    )
                    st.metadata_filter = kb_relaxed
                    st.stage1_path = "kb_fallback"
                    st.stage1_fallback_count += 1
                    st.cross_kb_fallback = True
            trace_collector.add_event(st.span, "stage1_fallback", "info",
                f"Stage1 0 匹配 → 放宽后 filter={st.metadata_filter}, path={st.stage1_path}",
                data={"relaxed_filter": st.metadata_filter,
                      "stage1_path": st.stage1_path,
                      "stage1_fallback_count": st.stage1_fallback_count})
        else:
            # fix f17：business_domain 不在 filter 中仍 0 命中 —— 元凶多半是
            # kb_id 推断失配（KBRouter 关键词规则把问题路由到无文档的 KB，
            # 如"报销"→policy_finance，但文档实际在 rag_test_kb）。
            # 与 business_domain 放宽同理：宁跨 KB 召回，不全量拒答；
            # 保留 doc_type 等语义收窄条件。
            fallback_filter = {
                k: v for k, v in st.metadata_filter.items()
                if k not in ("kb_id", "$or")
            }
            if fallback_filter != st.metadata_filter:
                logger.info(
                    f"ChunkLevelRetriever: metadata_filter {st.metadata_filter} 0 匹配, "
                    f"回退到放宽 kb_id 的检索"
                )
                st.metadata_filter = fallback_filter
                st.doc_ids = None
                st.stage1_path = "kb_fallback"
                st.stage1_fallback_count += 1
                st.cross_kb_fallback = True
                trace_collector.add_event(st.span, "stage1_fallback", "info",
                    f"Stage1 0 匹配 → 放宽 kb_id 后 filter={st.metadata_filter}",
                    data={"relaxed_filter": st.metadata_filter,
                          "stage1_path": st.stage1_path,
                          "stage1_fallback_count": st.stage1_fallback_count})

    def _stage2_hybrid_retrieve(self, st: "_Staging") -> None:
        """Stage 2：Chunk 级混合检索（向量+BM25+同义词扩展）+ 空召回重试。

        输入: st.query / st.doc_ids / st.metadata_filter / self.k
        输出: st.docs / st.seen / st.expanded_queries；重试发生时
              stage1_path 追加 +synonym_retry 后缀、fallback 计数 +1。
        """
        from backend.rag.preprocessing.synonyms import expand_query
        # 2026-08-20: 同义词扩展 — 对 query 做同义词扩展，提升口语化 query 召回
        # 优化：Stage 1 无匹配时跳过同义词扩展（无 doc 指引时扩展只会放大空检索）
        st.expanded_queries = expand_query(st.query) if st.doc_ids else None

        self._hybrid_collect(st, st.expanded_queries)

        # — 空召回降级：首查无扩展且 0 结果 → 补一次同义词扩展重试 —
        # 口语化 query（"发欧洲大概要多少天"）与书面文档词面零重叠，首查会被
        # 相似度阈值全过滤；空召回时补充候选是纯增益（rerank/Gate 照常把关），
        # 且只在空召回路径发生，正常请求零额外开销
        if not st.docs and st.expanded_queries is None:
            retry_expanded = expand_query(st.query)
            if len(retry_expanded) > 1:
                logger.info(
                    f"ChunkLevelRetriever: Stage 2 首查空召回, "
                    f"同义词扩展重试 ({len(retry_expanded) - 1} 个变体)"
                )
                st.stage1_path = f"{st.stage1_path}+synonym_retry"
                st.stage1_fallback_count += 1
                self._hybrid_collect(st, retry_expanded)
                from backend.observability.tracer import trace_collector
                trace_collector.add_event(st.span, "stage2_synonym_retry", "info",
                    f"Stage2 空召回 → 同义词扩展重试: {len(retry_expanded) - 1} 变体 → {len(st.docs)} chunks",
                    data={"expanded_queries": retry_expanded,
                          "output_count": len(st.docs)})

    def _hybrid_collect(self, st: "_Staging", expanded_queries) -> None:
        """执行一次 hybrid_retrieve，结果按 chunk_id 去重增量并入 st.docs/st.seen。

        首查与同义词重试共享同一 seen 集合——重试不会重复收录首查已命中的
        chunk，adaptive 扩展（_adaptive_expand）也依赖此集合的去重语义。
        """
        res = hybrid_retrieve(
            st.query, self.chunk_retriever, self.bm25,
            k=self.k, doc_ids=st.doc_ids,
            metadata_filter=st.metadata_filter,
            expanded_queries=expanded_queries,
        )
        for d in res:
            cid = d.metadata.get("chunk_id") or f'{d.metadata.get("doc_id","?")}:{d.metadata.get("chunk_index",0)}'
            if cid not in st.seen:
                st.seen.add(cid)
                st.docs.append(d)

    def _stage2_neighbor_fallback(self, st: "_Staging") -> "tuple[List[Document], dict] | None":
        """Stage 2 空结果 → Neighbor Expansion（拉文档全文）兜底。

        返回 (docs, metrics) 表示流程以兜底结束（调用方直接返回）；
        返回 None 表示有正常召回，继续走后处理。
        兜底结果同样过主体授权/跨库禁入收口（keep-set 强制优先于禁入剔除）。
        """
        if st.docs:
            return None
        logger.warning(f"ChunkLevelRetriever: Stage 2 无结果，尝试 Neighbor Expansion")
        fallback_docs = self._neighbor_expansion(st.query, st.doc_ids, st.metadata_filter)
        if st.authorized is not None and fallback_docs:
            fallback_docs = _keep_docs_in_allowed_kbs(fallback_docs, st.authorized, st.span)
        elif st.cross_kb_fallback and fallback_docs:
            fallback_docs = _drop_excluded_kb_fallback_docs(fallback_docs, st.span)
        if fallback_docs:
            logger.info(f"ChunkLevelRetriever: Neighbor Expansion → {len(fallback_docs)} chunks")
            return fallback_docs[: self.k], {
                "retrieved_chunks": len(fallback_docs),
                "stage1_path": st.stage1_path,
                "stage1_fallback_count": st.stage1_fallback_count,
                "fallback": "neighbor_expansion",
            }
        logger.warning(f"ChunkLevelRetriever: 降级也无结果")
        return [], {
            "retrieved_chunks": 0,
            "stage1_path": st.stage1_path,
            "stage1_fallback_count": st.stage1_fallback_count,
        }

    def _stage2_postprocess(self, st: "_Staging") -> None:
        """Stage 2 后处理：adaptive 扩 K → parent 上下文 → 授权收口过滤。

        输入: st.docs（非空，空召回已被 neighbor 兜底短路）
        输出: st.docs / st.effective_k（调用方按 effective_k 截断）
        """
        logger.info(f"ChunkLevelRetriever Stage 2: 召回 {len(st.docs)} 个 chunks")

        # ── Adaptive Retrieval: 质量不足时自动扩大 K ──
        st.docs, st.effective_k = self._adaptive_expand(st.query, st.docs, st.doc_ids,
                                         st.metadata_filter, st.seen)

        # ── Parent-Child 上下文增强：检索命中的 leaf，拉取对应 parent ──
        st.docs = attach_parent_context(st.docs, self._lookup_parents)

        # ── 收口过滤：adaptive 扩展可能再拉入文档，统一在此执行 ──
        # 已声明主体 → 白名单强制（无条件）；未声明 → 仅跨库兜底剔除 test 库
        if st.authorized is not None:
            st.docs = _keep_docs_in_allowed_kbs(st.docs, st.authorized, st.span)
        elif st.cross_kb_fallback:
            st.docs = _drop_excluded_kb_fallback_docs(st.docs, st.span)

    def _filter_has_docs(self, metadata_filter: dict) -> bool:
        """探测给定 metadata_filter 在 doc 库中是否还能匹配到文档（不做向量检索，零 embedding 成本）。

        Stage1 兜底放宽 business_domain 后调用：若放宽后的 KB 仍是空的
        （如 kb_id 指向无文档的 KB），则需要继续放宽 kb_id。
        探测异常时按"有文档"处理（返回 True），保持只放宽一级的旧行为。
        """
        try:
            res = self.doc_db.get(where=metadata_filter if metadata_filter else None)
            return bool((res or {}).get("ids"))
        except Exception as e:
            logger.debug(f"[ChunkLevelRetriever] filter 探测失败({type(e).__name__}): {e}")
            return True

    def _lookup_parents(self, chunk_ids: List[str]) -> List[Document]:
        """按 chunk_id 列表从向量库查 parent chunk（Parent-Child 上下文）。"""
        data = self.vectordb.get(where={"chunk_id": {"$in": chunk_ids}})
        ids = data.get("ids") or []
        documents = data.get("documents") or []
        metadatas = data.get("metadatas") or []
        result: List[Document] = []
        for i in range(len(ids)):
            result.append(Document(
                page_content=documents[i] if i < len(documents) else "",
                metadata=metadatas[i] if i < len(metadatas) else {},
            ))
        return result

    def _adaptive_expand(self, query: str, docs: list, doc_ids: list | None,
                         metadata_filter: dict, seen: set) -> "tuple[list, int]":
        """自适应 K 扩展：相似度不足或有效 chunk 太少时自动扩大检索范围。

        Returns:
            (docs, effective_k): 扩展后的文档列表和实际使用的 k 值。
            effective_k 用于调用方截断，不再修改 self.k（共享单例不可变）。
        """
        try:
            from backend.config import (
                ADAPTIVE_RETRIEVAL_ENABLED,
                ADAPTIVE_MIN_CHUNKS, ADAPTIVE_K_STEPS,
            )
        except ImportError:
            return docs, self.k

        if not ADAPTIVE_RETRIEVAL_ENABLED or not docs:
            return docs, self.k

        # 用 RRF/向量相似度（优先级: rrf_score > similarity > 0）
        scores = []
        for d in docs:
            s = d.metadata.get("rrf_score") or d.metadata.get("similarity") or 0
            try:
                scores.append(float(s))
            except (TypeError, ValueError):
                scores.append(0.0)

        # 用 chunk 数量和文档来源多样性判断质量
        unique_docs = len(set(d.metadata.get("doc_id", "") for d in docs if d.metadata.get("doc_id")))
        effective = sum(1 for d in docs if len(d.page_content.strip()) > 20)

        if unique_docs >= ADAPTIVE_MIN_CHUNKS or effective >= ADAPTIVE_MIN_CHUNKS * 2:
            return docs, self.k  # 已覆盖足够多文档/chunk

        logger.info(
            f"[Adaptive] 覆盖面不足 (unique_docs={unique_docs} < {ADAPTIVE_MIN_CHUNKS}, "
            f"effective={effective}) → 扩展检索"
        )

        effective_k = self.k
        # 逐级扩展 K 直到满足阈值或用尽步长
        for step_k in ADAPTIVE_K_STEPS:
            if step_k <= self.k:
                continue
            logger.info(
                f"[Adaptive] 覆盖面不足 → 扩展 k={self.k}→{step_k}"
            )
            extra = hybrid_retrieve(
                query, self.chunk_retriever, self.bm25,
                k=step_k, doc_ids=doc_ids,
                metadata_filter=metadata_filter,
            )
            new_count = 0
            for d in extra:
                cid = d.metadata.get("chunk_id") or f'{d.metadata.get("doc_id","?")}:{d.metadata.get("chunk_index",0)}'
                if cid not in seen:
                    seen.add(cid)
                    docs.append(d)
                    new_count += 1

            if new_count == 0:
                continue  # 没新文档，试下一步

            # 重新评估：文档来源数是否足够
            unique_docs = len(set(d.metadata.get("doc_id", "") for d in docs if d.metadata.get("doc_id")))
            effective = sum(1 for d in docs if len(d.page_content.strip()) > 20)
            if unique_docs >= ADAPTIVE_MIN_CHUNKS or effective >= ADAPTIVE_MIN_CHUNKS * 2:
                logger.info(f"[Adaptive] 扩展后达标: k={step_k}, docs={len(docs)}, unique={unique_docs}")
                effective_k = step_k
                break
        else:
            logger.info(f"[Adaptive] 扩展用尽，最终 docs={len(docs)}")

        return docs, effective_k

    def _neighbor_expansion(self, query: str, doc_ids: list | None,
                            metadata_filter: dict) -> List[Document]:
        """Stage 2 无结果时的 Context Expansion：用 doc 级 Chunk 替代。

        1. 有已知 doc_ids → 直接拉取这些文档的所有 chunk
        2. 否则 doc 级检索 → 拉取 chunk
        """
        from langchain_core.documents import Document

        if not doc_ids:
            # 没有已知 doc_ids，做一次 doc 级搜索
            try:
                doc_results = self.doc_db.similarity_search(
                    query, k=5,
                    filter=metadata_filter if metadata_filter else None,
                )
                doc_ids = [
                    d.metadata.get("doc_id") for d in doc_results
                    if d.metadata.get("doc_id")
                ]
            except Exception as e:
                # doc 级检索失败 → 视为无已知文档（软降级），留痕；后续会返回空结果走 NO_EVIDENCE 拒答
                logger.warning(
                    f"[ChunkLevelRetriever] Neighbor Expansion doc 检索失败: {e}",
                    exc_info=True,
                )
                doc_ids = []

        if not doc_ids:
            return []

        # 拉取文档全文
        try:
            results = self.doc_db.get(where={"doc_id": {"$in": doc_ids[:5]}})
            full_docs = []
            for i, content in enumerate(results.get("documents", [])):
                meta = results["metadatas"][i] if i < len(results.get("metadatas", [])) else {}
                full_docs.append(Document(page_content=content, metadata=meta))
            logger.info(f"ChunkLevelRetriever: Neighbor Expansion doc_ids={doc_ids[:5]} → {len(full_docs)} chunks")
            return full_docs
        except Exception as e:
            logger.error(f"ChunkLevelRetriever 降级失败: {e}")
            return []


# =====================================================
# Adaptive Retriever — 两阶段自适应检索
# =====================================================

class AdaptiveRetriever(BaseRetriever):
    """自适应检索器：chunk 检索 → Cluster 检测 → Context Expansion

    Stage 1: base_retriever 做 chunk 级检索
    Stage 2: 统计 doc_id 分布，检测命中是否集中在少数文档
            如果集中在 ≤ max_cluster_docs 个文档 → Context Expansion（邻近 Chunk / 同级 Heading Chunk）
            如果分散 → 仅用 chunks，避免上下文污染
    """

    base_retriever: BaseRetriever = Field(description="chunk 级检索器")
    doc_db: object = Field(description="文档级 ChromaDB，用于 Context Expansion")
    cluster_threshold: float = Field(default=ADAPTIVE_CLUSTER_THRESHOLD, description="单文档占比阈值")
    max_cluster_docs: int = Field(default=ADAPTIVE_MAX_CLUSTER_DOCS, description="触发 Expansion 的最大文档数")

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        from backend.observability.tracer import trace_collector

        # 治理 A：扩展阶段契约 span —— 扩展触发/替换/驱逐全程留痕
        # （无 active trace 时 start_span 返回 noop，安全降级）
        span = trace_collector.start_span(
            "adaptive_expansion", name="Adaptive Context Expansion", type="retrieval",
        )

        def _cname(c) -> str:
            m = c.metadata or {}
            return m.get("source") or m.get("source_file") or str(m.get("doc_id") or "?")

        def _end(metrics_extra: dict | None = None, event_data: dict | None = None):
            if metrics_extra:
                trace_collector.end_span(span, metrics={
                    "input_chunks": len(chunks), "output_chunks": len(result),
                    "clustered_docs": len(clustered),
                    **metrics_extra,
                })
            else:
                trace_collector.end_span(span, metrics={
                    "input_chunks": len(chunks), "output_chunks": len(chunks),
                    "clustered_docs": 0,
                })
            if event_data:
                trace_collector.add_event(
                    span, "adaptive_expansion_decision", "info",
                    f"Adaptive: {event_data.get('decision', '')}", data=event_data,
                )

        chunks = self.base_retriever.invoke(query)
        result = chunks
        clustered = []
        if not chunks:
            _end({"skipped": "empty"})
            return []

        doc_counter = Counter()
        for c in chunks:
            doc_id = c.metadata.get("doc_id")
            if doc_id:
                doc_counter[doc_id] += 1

        total = len(chunks)

        clustered = [
            doc_id for doc_id, count in doc_counter.items()
            if count / total >= self.cluster_threshold
        ]

        if not (clustered and len(clustered) <= self.max_cluster_docs):
            _end({"skipped": "no_cluster"})
            return result

        cluster_set = set(clustered)

        # ── 置信度门控：cluster chunk 分数太低时跳过扩展 ──
        # 低分说明 reranker 无法区分相关/噪声，扩展只会放大噪声
        cluster_scores = [
            s for s in (
                c.metadata.get("rerank_score")
                or c.metadata.get("rrf_score")
                or c.metadata.get("similarity")
                for c in chunks
                if c.metadata.get("doc_id") in cluster_set
            )
            if s
        ]
        if cluster_scores:
            avg_score = sum(cluster_scores) / len(cluster_scores)
            top_score = max(cluster_scores)
            if avg_score < 0.5 and top_score < 0.7:
                logger.info(
                    f"AdaptiveRetriever: Cluster 检测 (docs={clustered}) "
                    f"但置信度低 (avg={avg_score:.3f}, top={top_score:.3f}) → 跳过 Expansion"
                )
                _end({"skipped": "low_confidence"}, {
                    "decision": "skip_low_confidence",
                    "clustered_docs": [_cname(c) for c in chunks if c.metadata.get("doc_id") in cluster_set][:5],
                    "avg_score": round(avg_score, 3),
                    "top_score": round(top_score, 3),
                })
                return chunks

        logger.info(f"AdaptiveRetriever: Cluster 检测 (docs={clustered}, {len(clustered)}/{len(doc_counter)}) → Context Expansion")
        try:
            results = self.doc_db.get(where={"doc_id": {"$in": clustered}})
            full_doc_map = {}
            for i, content in enumerate(results["documents"]):
                doc_id = results["metadatas"][i].get("doc_id")
                if doc_id:
                    full_doc_map[doc_id] = Document(
                        page_content=content,
                        metadata=results["metadatas"][i],
                    )

            # ── 替换而非前置：full doc 替换其 source chunks，保持排序 ──
            # 旧实现 `full_docs + chunks` 把全文档放在最前面，导致：
            #   1) 结果数膨胀（N full + M chunks），下游 top-k 截断丢失相关 chunk
            #   2) 干扰文档全文排在相关 chunk 前面，排挤正确内容
            # 新实现：原位替换，每个 cluster doc 的第一个 chunk 位置放全文，
            # 同 doc 的后续 chunk 移除，非 cluster chunk 保持原位。
            seen_cluster_docs = set()
            result = []
            for c in chunks:
                doc_id = c.metadata.get("doc_id")
                if doc_id in full_doc_map:
                    if doc_id not in seen_cluster_docs:
                        seen_cluster_docs.add(doc_id)
                        result.append(full_doc_map[doc_id])
                    # 同 doc 后续 chunk 跳过（已被全文替代）
                else:
                    result.append(c)

            replaced_names = [_cname(full_doc_map[did]) for did in seen_cluster_docs]
            kept_names = [_cname(c) for c in chunks if c.metadata.get("doc_id") not in cluster_set]
            _end({"replaced_chunks": len(chunks) - len(result)}, {
                "decision": "expanded",
                "clustered_docs": replaced_names,
                "kept_non_cluster_chunks": len(kept_names),
                "note": "cluster doc 原位替换为全文记录；非 cluster chunk 原位保留",
            })
            logger.info(
                f"AdaptiveRetriever: 扩展替换 {replaced_names}，"
                f"非 cluster chunk 保留 {len(kept_names)} 条"
            )
            return result
        except Exception as e:
            logger.error(f"AdaptiveRetriever: Context Expansion 失败: {e}")
            _end({"fallback": "passthrough"}, {
                "decision": "expansion_failed_passthrough",
                "error": str(e)[:200],
            })
        return chunks
