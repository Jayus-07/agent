"""RAG 阶段时延、检索质量与分块指标。"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

rag_consistency_issues_total = Counter(
    "rag_consistency_issues_total",
    "索引五路存储一致性检查检出的问题数（按存储与严重级别）",
    labelnames=("store", "severity"),  # store: registry|chroma_chunk|chroma_doc|chunk_store|bm25; severity: error|warning
)


rag_consistency_repairs_total = Counter(
    "rag_consistency_repairs_total",
    "Sweeper 执行的修复动作数（按存储与结果）",
    labelnames=("store", "result"),  # result: ok | failed
)


# ── 运营指标（2026-08-11 新增）──
# 累计计数（用于计算 rates）
rag_query_total = Counter(
    "rag_query_total",
    "RAG 查询总数（按结果状态）",
    labelnames=("status",),  # hit | rejected | fallback
)


rag_upload_total = Counter(
    "rag_upload_total",
    "RAG 上传索引终态总数（按结果状态）",
    labelnames=("status",),  # success | duplicate | failed
)


rag_index_duration_seconds = Histogram(
    "rag_index_duration_seconds",
    "RAG 文档从索引开始到终态的耗时（秒）",
    labelnames=("status",),
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
)


# RAG 防线异常软降级计数（R3 fail-visible）：fail-open 语义保留，
# 但每次异常放行必须可见。layer: pre_wrap | gate1 | gate1_deserialize |
# gate1_entity | risk_level | gate2 | claim_verify | faithfulness | prompt
rag_gate_degraded_total = Counter(
    "rag_gate_degraded_total",
    "RAG 防线异常软降级总数（按防线层）",
    labelnames=("layer",),
)


# RAG 生成前短路计数（区分空检索与 Gate 前置拒答，此前两者 trace 不可区分）
rag_short_circuit_total = Counter(
    "rag_short_circuit_total",
    "RAG 生成前短路总数（按原因）",
    labelnames=("reason",),  # empty_retrieval | evidence_gate
)


rag_permission_filtered_total = Counter(
    "rag_permission_filtered_total",
    "RAG 生成上下文中被文档级权限过滤剔除的越权证据条数（§4 权限范围消费方）",
)


rag_version_filtered_total = Counter(
    "rag_version_filtered_total",
    "RAG 生成上下文中被版本窗口过滤剔除的证据条数（§6 版本治理消费方，R4）",
)


# ── RAG 检索请求级 Embedding 复用观测（R0/R1-A，2026-10-12）──
# 为什么存在：一次问答曾在 Doc/Chunk/Hybrid/MultiQuery 路径各自重嵌同一查询
# （审计 P2-1 实测 6 次）。本组指标区分「真实出站调用」与「请求内/进程内复用」，
# 标签只取低基数枚举；原始 query、request_id、tenant 只进 Trace，不进时序库。
rag_embedding_calls_total = Counter(
    "rag_embedding_calls_total",
    "Embedding 真实出站调用次数（按 kind 与 provider 低基数枚举）",
    labelnames=("kind", "provider"),  # kind: query | documents
)


rag_embedding_cache_total = Counter(
    "rag_embedding_cache_total",
    "Embedding 命中复用的次数（按来源）",
    labelnames=("hit_source",),  # request | process
)


rag_embedding_dedup_ratio = Histogram(
    "rag_embedding_dedup_ratio",
    "单请求 embedding 去重比 = 1 - 真实出站调用数 / 请求内 embedding 总请求数",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)


rag_embedding_duration_seconds = Histogram(
    "rag_embedding_duration_seconds",
    "Embedding 真实出站调用耗时（秒，按 kind）",
    labelnames=("kind",),  # query | documents
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
)


rag_embedding_request_total = Counter(
    "rag_embedding_request_total",
    "单请求 embedding 统计（按结果归类，用于核对去重比与出站次数）",
    labelnames=("result",),  # computed | request_hit | process_hit
)


rag_embedding_call_errors_total = Counter(
    "rag_embedding_call_errors_total",
    "Embedding 出站失败/超时次数（按 kind 与状态；失败不写 token 消费）",
    labelnames=("kind", "status"),  # status: error
)


rag_embedding_requested_per_scope = Histogram(
    "rag_embedding_requested_per_scope",
    "每个 embedding 作用域的向量化请求次数（主请求 + retrieval 线程池自建作用域）",
    buckets=(1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 50),
)


# ── RAG 阶段时延（2026-10-06 Grafana 观测重构，Grafana 04 页长期时序）──
# stage 固定低基数枚举，禁止扩展：
#   retrieve = 检索主阶段（BM25+向量+同文档扩展，到 context 就绪为止，含内部 rerank）
#   rerank   = RerankCompressor 压缩耗时（retrieve 的子阶段）
#   embedding = 检索期 query 向量化累计耗时（R1-A：区分出站与复用，2026-10-12）
#   generate = LLM 生成（context 就绪 → chain.invoke 返回）
#   total    = RAGChain.ask() 全链（含 verify/gate）
rag_stage_duration_seconds = Histogram(
    "rag_stage_duration_seconds",
    "RAG 阶段耗时（秒，按固定低基数 stage 枚举）",
    labelnames=("stage",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80, 160),
)


def record_rag_stage(stage: str, duration_ms: float) -> None:
    """rag_stage_duration_seconds 记账入口（挂点跨文件复用；软失败）。

    stage 固定枚举 retrieve | rerank | generate | total，调用方传入其他值
    会扩张 Prometheus 序列——此处不做白名单拦截以保持旁路零开销，
    新 stage 值必须先改上面 Histogram 注释与 Grafana 面板。
    """
    try:
        rag_stage_duration_seconds.labels(stage=stage).observe(
            max(float(duration_ms), 1) / 1000)
    except Exception:  # noqa: BLE001 — 指标旁路软失败
        pass


# ── 检索线程池 wait/exec 观测（Phase 3，2026-10-07，spec §20）──
# 接线点：infra/thread_pools.py::submit_rag_task（唯一提交出口）。
# pool ∈ multi_query | outer | inner（低基数固定枚举）。
# wait_ms 是池饥饿的直接信号——父子任务共池回潮时 wait 会先爆炸。
rag_pool_wait_seconds = Histogram(
    "rag_pool_wait_seconds",
    "检索线程池排队等待时长（秒，submit→开始执行）",
    labelnames=("pool",),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


rag_pool_task_seconds = Histogram(
    "rag_pool_task_seconds",
    "检索线程池任务自身执行时长（秒）",
    labelnames=("pool",),
    buckets=(0.005, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
)


rag_pool_queued_tasks = Gauge(
    "rag_pool_queued_tasks",
    "任务提交时刻的池队列深度（采样值）",
    labelnames=("pool",),
)


def record_rag_pool_timing(
    pool: str, wait_ms: float, exec_ms: float, queued: int | None = None,
) -> None:
    """检索池 wait/exec/队列深度记账入口（submit_rag_task 专用；软失败）。"""
    try:
        rag_pool_wait_seconds.labels(pool=pool).observe(max(float(wait_ms), 0) / 1000)
        rag_pool_task_seconds.labels(pool=pool).observe(max(float(exec_ms), 0) / 1000)
        if queued is not None:
            rag_pool_queued_tasks.labels(pool=pool).set(int(queued))
    except Exception:  # noqa: BLE001 — 指标旁路软失败
        pass


# NLI 监控（2026-08-11）：超时次数 + 当前未覆盖率
nli_timeout_total = Counter(
    "nli_timeout_total",
    "NLI 推理超时次数（触发 fallback）",
)


nli_coverage_rate = Gauge(
    "nli_coverage_rate",
    "NLI 有效校验率（0-1，1 = 无超时）",
)


# 实时 rate（Gauge 缓存最新计算值）
rag_hit_rate = Gauge(
    "rag_hit_rate",
    "RAG 命中率（0-1）。告警阈值 < 0.7",
)


rag_reject_rate = Gauge(
    "rag_reject_rate",
    "RAG 拒答率（0-1，Evidence Gate 拦截）。告警阈值 > 0.3",
)


doc_metadata_coverage = Gauge(
    "doc_metadata_coverage",
    "活跃文档 metadata 完整度（0-1）。告警阈值 < 0.8",
)


# ── 埋点 helpers（2026-08-11 新增）──
_rag_state = {"hit": 0, "rejected": 0, "fallback": 0}


def record_rag_status(status: str) -> None:
    """埋点 RAG 查询结果（hit / rejected / fallback）并更新实时 rate gauge。

    使用:
        from backend.observability.metrics import record_rag_status
        record_rag_status("hit")
    """
    if status not in _rag_state:
        return
    _rag_state[status] += 1
    rag_query_total.labels(status=status).inc()
    total = _rag_state["hit"] + _rag_state["rejected"] + _rag_state["fallback"]
    if total > 0:
        rag_hit_rate.set((_rag_state["hit"] + _rag_state["fallback"]) / total)
        rag_reject_rate.set(_rag_state["rejected"] / total)


def update_metadata_coverage() -> None:
    """扫描 doc_registry 计算活跃文档 metadata 完整度（异步调用）。

    完整定义: doc_type + business_domain + summary 都有值的 active 文档 / 总 active 文档。
    R1: 改走 DocumentRegistry（支持 SQLite/PG 双后端），原先硬编码路径的裸 sqlite
    读在 PG 模式下会读到过期数据。
    """
    try:
        from backend.config.database import DOC_REGISTRY_PATH
        from backend.rag.indexing.doc_registry import DocumentRegistry
        rows = DocumentRegistry(DOC_REGISTRY_PATH).list_active()
        total = len(rows)
        if total > 0:
            complete = sum(
                1 for r in rows
                if r.get("doc_type") is not None and r.get("doc_type") != "general"
                and r.get("business_domain") is not None and r.get("business_domain") != "general"
                and r.get("summary") is not None and r.get("summary") != ""
            )
            doc_metadata_coverage.set(complete / total)
    except Exception:
        # registry 可能尚未初始化（首次启动）
        pass


