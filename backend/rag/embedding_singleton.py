"""Embedding 模型全局单例 — 双模式 (Cloud/Local) + Token Tracking.

架构设计 (P0 - 硬性约束):
1. EMBEDDING_PROVIDER=cloud → Cloud Embedding (OpenAIEmbeddings，DashScope/SiliconFlow 等)
2. EMBEDDING_PROVIDER=local → Local Embedding (HuggingFaceEmbeddings)
3. Cloud 模式必须校验数据库供应商 API Key，不存在时明确报错
4. 禁止 Cloud 配置错误时静默降级到 Local
5. Token Tracker 记录用量，Local 模式无法获取 usage 时 total_tokens=null

EMBEDDING_PROVIDER 留空时跟随 ENV_MODE（向后兼容）。

用法:
    from backend.rag.embedding_singleton import get_embedding
    embedding = get_embedding()
"""
from __future__ import annotations

import contextvars
import hashlib
import math
import os
import threading
import time
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List

from langchain_core.embeddings import Embeddings
# langchain_huggingface 顶层 import 会连带 torch（~6s/进程），只在实际
# 加载本地模型时才引入 —— 见 _get_local_embedding 内的延迟导入。

from backend.config import (
    ENV_MODE,
    EMBEDDING_PROVIDER,
    EMBEDDING_MODEL,
    EMBEDDING_MODEL_PATH,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_REQUEST_TIMEOUT,
    TOKEN_USAGE_LOG_PATH,
)
from backend.config import model_roles
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import specialized as specialized_mod
from backend.infra.token_tracker import create_tracker_for_embedding
from backend.shared.logger import logger

# =====================================================
# Factory Pattern - Cloud vs Local
# =====================================================


def _configured_embedding_model() -> str:
    """读取向量化角色；无 DB 覆盖时保持历史配置常量。"""
    return model_roles.resolve_runtime_name("embedding", EMBEDDING_MODEL)


def _metered_embedding_model() -> str:
    """返回「实际出站调用」的模型名，供用量计量与计费对齐。

    实际请求用的模型名来自专项绑定的派生结果（注册表以角色覆盖优先）；
    计费按 `model_price` 里以**这个名字**登记的条目查价。此前记录端用
    `_configured_embedding_model()`（纯角色/env 解析），在「角色未绑定但
    专项通道生效」时会与真实调用名错位，导致录了价也对不上号。
    """
    binding = specialized_mod.resolve_binding("embedding")
    if binding is not None and str(binding.model_name or "").strip():
        return str(binding.model_name).strip()
    return _configured_embedding_model()


def _metered_embedding_provider() -> str:
    """返回专项绑定的真实供应商 ID，供缓存身份和用量账本使用。"""
    binding = specialized_mod.resolve_binding("embedding")
    if binding is not None and str(binding.provider_id or "").strip():
        return str(binding.provider_id).strip()
    return "cloud"


def _embedding_runtime_signature() -> tuple[Any, ...]:
    """返回影响 embedding 客户端的配置签名，供已构建的包装器热切换。"""
    binding = specialized_mod.resolve_binding("embedding")
    if binding is not None:
        return (
            "specialized",
            binding.provider_id,
            binding.adapter,
            binding.model_name,
            binding.base_url,
            repr(sorted(dict(binding.options).items())),
            credentials_mod.credentials_version(binding.provider_id),
        )
    return (
        "database-unconfigured",
        EMBEDDING_PROVIDER,
        _configured_embedding_model(),
    )


def _embedding_is_cloud() -> bool:
    """判断当前 embedding 是否应走远端协议。"""
    return (
        EMBEDDING_PROVIDER == "cloud"
        or specialized_mod.resolve_binding("embedding") is not None
    )


def _resolve_cloud_embedding_config() -> dict[str, Any]:
    """解析云端 embedding 出站配置；数据库专项绑定是唯一配置来源。

    都用 DB（2026-09-21 拍板）：无绑定时不再回退旧 env（EMBEDDING_API_KEY/BASE），
    返回空配置，由 `_get_cloud_embedding` 以明确报错拒绝，提示先在管理端绑定。
    """
    binding = specialized_mod.resolve_binding("embedding")
    if binding is None:
        return {
            "model": "",
            "api_key": "",
            "base_url": "",
            "dimensions": None,
            "provider": "",
        }

    credentials = credentials_mod.resolve_credentials(
        binding.provider_id,
        model_name=binding.model_name,
    )
    dimensions = binding.options.get("dimensions")
    from backend.infra.llm import models as models_mod

    provider_entry = models_mod.get_provider_entry(binding.provider_id) or {}
    return {
        # 登记名/上游名拆分（2026-09-22）：发给厂商 API 的 model 用 upstream。
        "model": binding.upstream_name or binding.model_name,
        "api_key": credentials.api_key or "",
        "base_url": binding.base_url,
        "dimensions": int(dimensions) if dimensions is not None else None,
        "provider": binding.provider_id,
        "network_scope": provider_entry.get("network_scope", "public"),
    }


def _get_cloud_embedding(
    runtime_config: dict[str, Any] | None = None,
    *,
    request_timeout: float | None = None,
    batch_size: int | None = None,
    max_retries: int = 1,
) -> Embeddings:
    """获取 Cloud Embedding (DashScope OpenAI 兼容)."""
    runtime_config = runtime_config or _resolve_cloud_embedding_config()
    timeout = EMBEDDING_REQUEST_TIMEOUT if request_timeout is None else request_timeout
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("embedding 请求超时必须为有限正数")
    batch_size = EMBEDDING_BATCH_SIZE if batch_size is None else batch_size
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size <= 0
    ):
        raise ValueError("embedding 请求批大小必须为正整数")
    if not runtime_config["api_key"]:
        raise RuntimeError(
            "数据库未配置可用的 embedding 供应商 API Key，请先在管理端测试并保存"
            "向量模型；如需本地模型，请显式使用本地模式。"
        )
    
    try:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    except ImportError:
        raise ImportError(
            "Cloud 模式需要 langchain-openai: pip install langchain-openai"
        )
    
    embedding_kwargs: dict[str, Any] = {
        "model": runtime_config["model"],
        "api_key": runtime_config["api_key"],
        "base_url": runtime_config["base_url"],
        # DashScope 兼容模式只接受字符串数组；默认开启的 tiktoken 分词会把文本
        # 转成 token id 数组发给 API，触发 400 "contents is neither str nor
        # list of str"，导致启动时全量索引重建失败（chroma 0 embeddings）。
        # 关闭本地分词后直接发原文。
        "check_embedding_ctx_length": False,
        # 单次请求携带的文本条数（EMBEDDING_BATCH_SIZE，默认 10）：
        # DashScope text-embedding-v3 上限 10；换 SiliconFlow / TEI 时可
        # 在 .env 调大到 32+ 提速索引。
        "chunk_size": batch_size,
        # 让 HTTP 请求在专家总超时前自行结束，不能依赖外层线程中断。
        "timeout": timeout,
        # openai SDK 默认失败重试 2 次：检索期 embedding 故障会被放大成
        # 3 × timeout 的串行等待，显式压到 1 次（首次 + 1 重试），配合
        # EMBEDDING_REQUEST_TIMEOUT 限制最坏单调用耗时。
        # 离线重建传 0，由任务级重试复用候选，避免超时后叠加长推理请求。
        "max_retries": max_retries,
    }
    if runtime_config["dimensions"] is not None:
        embedding_kwargs["dimensions"] = runtime_config["dimensions"]
    if runtime_config.get("network_scope") == "private":
        import httpx

        # 已明确登记的内网服务直连，避免继承宿主机公网代理后误转发。
        embedding_kwargs["http_client"] = httpx.Client(trust_env=False)
        embedding_kwargs["http_async_client"] = httpx.AsyncClient(trust_env=False)
    embedding = OpenAIEmbeddings(**embedding_kwargs)
    
    logger.info(
        "[Embedding] Cloud 模式初始化完成 "
        f"(model={runtime_config['model']}, api_base={runtime_config['base_url']})"
    )
    return embedding


def _get_local_embedding() -> Embeddings:
    """获取 Local Embedding (HuggingFace BGE)."""
    # 延迟解析设备：resolve_eval_device() 首次调用才 import torch，
    # 避免本模块在导入链上时所有进程陪跑 ~6s 的 torch 导入。
    from backend.config.llm import resolve_eval_device
    from langchain_huggingface import HuggingFaceEmbeddings
    device = resolve_eval_device()
    embedding = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_PATH,
        model_kwargs={"device": device},
    )

    logger.info(
        "[Embedding] Local 模式初始化完成 "
        f"(model={EMBEDDING_MODEL_PATH}, device={device})"
    )
    return embedding


# =====================================================
# 请求级 Embedding 复用（R1-A，2026-10-12）
# =====================================================
# 为什么存在：同一次问答里，Doc 级相似度检索、Chunk 级 hybrid 向量腿、
# MultiQuery 变体、同义词扩展会各自调用 embed_query——审计 P2-1 实测单次
# 问答 6 次出站向量化（每次 0.3~2.8s 远程 RTT + 计费），而其中多次文本
# 完全相同（同一句 query 只因检索 k 不同被重复向量化）。
#
# 设计边界（与"检索结果缓存"严格区分）：
#   - 本模块只缓存 **embedding 结果**，键 = 模型身份 + 实际向量化输入；
#     不含 k / filter / KB / tenant——那些属于检索结果缓存的键，继续由
#     RagRequestState.retrieval_cache 承担，两套缓存不互相代替。
#   - 文本按原样参与哈希，不做归一化/去前缀/截断：不同文本变体永不共用向量。
#   - 模型身份包含 provider/model/dimension 与配置签名，换模型即换命名空间，
#     绝不跨模型复用向量（与 pgvector rag_index_meta 的 INDEX_EMBEDDING_MISMATCH 同口径）。
#   - 失败调用不入缓存；进程级缓存只存向量与哈希，不保留原始 query 明文。
#   - 请求级缓存生命周期 = 一次 RAG 请求（begin/end 成对，end 清理计数并上报）。

# 进程级缓存条目上限（跨请求复用）。256 条 × 1024 维 float ≈ 2MB 量级，
# 有界且远小于任何索引结构；超限按 LRU 淘汰。
_EMBED_PROCESS_CACHE_MAX = max(0, int(os.getenv("RAG_EMBED_CACHE_MAX", "256")))
# 是否允许跨请求复用向量。请求内去重不受此开关影响（它只影响 provider 调用次数）。
_EMBED_PROCESS_CACHE_ENABLED = (
    os.getenv("RAG_EMBED_PROCESS_CACHE_ENABLED", "true").strip().lower()
    in ("1", "true", "yes", "on")
)

_REQUEST_EMBEDS: contextvars.ContextVar = contextvars.ContextVar(
    "rag_request_embeddings", default=None
)
# ── 单飞并发机制（P1-C 修复：淘汰式 key 锁 → 固定条纹锁）─────────
# 旧实现维护 key→Lock 的 LRU 表，达到 64 时淘汰"最久未用"的锁——但那个锁
# 可能正在被等待/执行，淘汰后同 key 会再建第二把锁，导致等待者重复出站；
# 关闭进程缓存后该竞态更明显。
# 现在改为：key 的哈希取模固定宽度锁池（条纹锁）。
#   - 锁数量恒定（_EMBED_LOCK_STRIPES），不淘汰、不增长，无"淘汰在用锁"可能；
#   - 不同 key 落在不同条纹上 → 保留不同 query 的并发能力；只有哈希碰撞才串行；
#   - 同一 key 恒映射同一把锁 → 永远不存在第二把锁。
# 在途登记（_EMBED_INFLIGHT）用于「失败即解除、等待者重试」的精确语义，键在
# 持有条纹锁期间增删，同一个 key 同时只有一条；它不是锁表，不参与淘汰。
_EMBED_LOCK_STRIPES = max(1, int(os.getenv("RAG_EMBED_LOCK_STRIPES", "64")))
_EMBED_STRIPE_LOCKS: list = [threading.Lock() for _ in range(_EMBED_LOCK_STRIPES)]
_EMBED_INFLIGHT: dict = {}
_EMBED_LOCKS_GUARD = threading.Lock()


def _stripe_index(key: str) -> int:
    """key → 固定条纹下标（同一 key 恒同锁；不同 key 允许哈希碰撞串行）。"""
    digest = hashlib.blake2b(key.encode("utf-8", "surrogatepass"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % _EMBED_LOCK_STRIPES


def _stripe_lock(key: str) -> threading.Lock:
    return _EMBED_STRIPE_LOCKS[_stripe_index(key)]


@dataclass
class EmbeddingRequestStats:
    """单请求 embedding 统计（R0 指标口径：调用/唯一输入/复用）。

    counts 语义：
      requested  本请求对 embedding 的总请求次数（含被复用掉的）
      computed   真实出站调用次数
      request_hits / process_hits  请求内 / 进程级复用命中次数
      unique_inputs  本请求出现过的不同 (模型身份, 文本) 数
    """

    requested: int = 0
    computed: int = 0
    request_hits: int = 0
    process_hits: int = 0
    duration_ms: float = 0.0
    kinds: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    # 真实向量的维度（成功或缓存命中时记录）：用于验证"维度确实被校验"，
    # 也为后续对账（与索引 1024 维）留证据。
    vector_dim: int | None = None
    # 进程级缓存的快照来源（汇总视图用；单线程作用域保持 "request"）
    hit_source: str = "request"

    @property
    def unique_inputs(self) -> int:
        return len(self.values)

    @property
    def dedup_ratio(self) -> float:
        if self.requested <= 0:
            return 0.0
        return max(0.0, 1.0 - self.computed / float(self.requested))

    def as_dict(self) -> dict:
        return {
            "requested": self.requested,
            "computed": self.computed,
            "request_hits": self.request_hits,
            "process_hits": self.process_hits,
            "unique_inputs": self.unique_inputs,
            "dedup_ratio": round(self.dedup_ratio, 4),
            "duration_ms": round(self.duration_ms, 1),
            "vector_dim": self.vector_dim,
            "kinds": dict(self.kinds),
        }


@dataclass
class _RequestEmbeddingRecord:
    """一次 RAG 请求的 embedding 总账（跨线程汇总）。

    P1-B 修复要点：检索变体跑在 retrieval 线程池，池线程的 contextvar 是空的，
    旧实现让每个线程自建一个"作用域"并在收尾就上报——父请求读到的统计因此
    只有主线程那一份（实测 provider 真实出站 4 次、父请求只记 1 次）。
    现在：主线程建 record 并随 contextvars 一起被 copy_context 带进池线程，
    每个线程只贡献自己的 EmbeddingRequestStats，最终由父请求统一汇总读取；
    record 同时保证嵌套请求不重复统计（record 只在最外层 begin 创建）。
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    scopes: list = field(default_factory=list)
    created_monotonic: float = field(default_factory=time.monotonic)

    def add(self, scope: "EmbeddingRequestStats") -> None:
        with self.lock:
            self.scopes.append(scope)

    def snapshot(self) -> "EmbeddingRequestStats":
        """按作用域求和（每个 provider 调用恰好落在一个作用域里，不重不漏）。"""
        total = EmbeddingRequestStats()
        with self.lock:
            scopes = list(self.scopes)
        for scope in scopes:
            total.requested += scope.requested
            total.computed += scope.computed
            total.request_hits += scope.request_hits
            total.process_hits += scope.process_hits
            total.duration_ms += scope.duration_ms
            for k, v in scope.kinds.items():
                total.kinds[k] = total.kinds.get(k, 0) + v
            for k, v in scope.values.items():
                total.values.setdefault(k, v)
            if scope.vector_dim is not None:
                total.vector_dim = scope.vector_dim
        total.hit_source = (
            "request" if total.request_hits >= total.process_hits else "process")
        return total

    # 读取面与 EmbeddingRequestStats 对齐（调用方拿到的总账可直接读计数）
    @property
    def unique_inputs(self) -> int:
        return self.snapshot().unique_inputs

    @property
    def requested(self) -> int:
        return self.snapshot().requested

    @property
    def computed(self) -> int:
        return self.snapshot().computed

    @property
    def request_hits(self) -> int:
        return self.snapshot().request_hits

    @property
    def process_hits(self) -> int:
        return self.snapshot().process_hits

    @property
    def duration_ms(self) -> float:
        return self.snapshot().duration_ms

    @property
    def dedup_ratio(self) -> float:
        return self.snapshot().dedup_ratio

    def as_dict(self) -> dict:
        return self.snapshot().as_dict()


_EMBED_REQUEST_RECORD: contextvars.ContextVar = contextvars.ContextVar(
    "rag_embedding_request_record", default=None
)
# P1-C：标记「本线程是否本次调用中自建了作用域」，自建者负责 flush+上报。
_EMBED_SCOPE_OWNER: contextvars.ContextVar = contextvars.ContextVar(
    "rag_embedding_scope_owner", default=False
)


def begin_embedding_request(*, reset: bool = False) -> "_RequestEmbeddingRecord":
    """打开请求级 embedding 总账（幂等：已有则复用，不重置计数）。

    调用者：RAGChain._execute 在检索入口开、finally 关。返回**总账对象**而非
    快照：跨线程子作用域的贡献会随调用推进自动出现在它上面（requested /
    computed / request_hits / process_hits 等按需实时汇总）。
    """
    record = _EMBED_REQUEST_RECORD.get()
    if record is None or reset:
        record = _RequestEmbeddingRecord()
        _EMBED_REQUEST_RECORD.set(record)
    scope = EmbeddingRequestStats()
    record.add(scope)
    _REQUEST_EMBEDS.set(scope)
    _EMBED_SCOPE_OWNER.set(False)
    return record


def _embedding_scope() -> EmbeddingRequestStats:
    """取本线程当前作用域；缺失（池线程）则自建并登记进请求总账。"""
    scope = _REQUEST_EMBEDS.get()
    if scope is not None:
        return scope
    scope = EmbeddingRequestStats()
    record = _EMBED_REQUEST_RECORD.get()
    if record is not None:
        record.add(scope)
    _REQUEST_EMBEDS.set(scope)
    _EMBED_SCOPE_OWNER.set(True)
    return scope


def _flush_owned_scope() -> None:
    """自建作用域者收尾时清理线程本地引用（计数已在总账里，无需上报）。"""
    if not _EMBED_SCOPE_OWNER.get():
        return
    _REQUEST_EMBEDS.set(None)
    _EMBED_SCOPE_OWNER.set(False)


def end_embedding_request() -> EmbeddingRequestStats | None:
    """关闭请求级总账并返回完整汇总（调用方负责上报指标/写 Trace）。

    必须与 begin 成对：线程池复用下残留的请求缓存会让下一次请求误命中。
    """
    record = _EMBED_REQUEST_RECORD.get()
    _REQUEST_EMBEDS.set(None)
    _EMBED_SCOPE_OWNER.set(False)
    _EMBED_REQUEST_RECORD.set(None)
    if record is None:
        return None
    return record.snapshot()


def current_embedding_stats() -> EmbeddingRequestStats | None:
    """读取当前请求的实时汇总（不清理）。无请求总账时返回 None。"""
    record = _EMBED_REQUEST_RECORD.get()
    if record is None:
        return None
    return record.snapshot()


def report_embedding_request(stats: EmbeddingRequestStats | None) -> None:
    """把单请求 embedding 统计上报到低基数 Prometheus（软失败）。

    上报口径与 Trace 事件同源：同一份 EmbeddingRequestStats 既进指标也进
    span event，避免"指标说 3 次、Trace 说 6 次"的两套口径。
    """
    if stats is None or stats.requested <= 0:
        return
    try:
        from backend.observability.metrics import (
            rag_embedding_dedup_ratio,
            rag_embedding_request_total,
            rag_embedding_requested_per_scope,
        )

        rag_embedding_dedup_ratio.observe(stats.dedup_ratio)
        rag_embedding_requested_per_scope.observe(stats.requested)
        for field_name, result in (
            ("computed", "computed"),
            ("request_hits", "request_hit"),
            ("process_hits", "process_hit"),
        ):
            count = int(getattr(stats, field_name, 0) or 0)
            if count:
                rag_embedding_request_total.labels(result=result).inc(count)
    except Exception:  # noqa: BLE001 — 指标旁路软失败
        pass


def reset_embedding_cache() -> None:
    """清空进程级 embedding 缓存与在途登记（配置热切换/测试隔离用）。"""
    with _EMBED_LOCKS_GUARD:
        _PROCESS_EMBED_CACHE.clear()
        _EMBED_INFLIGHT.clear()


# 与 pgvector 向量维度同源（text-embedding-v3 = 1024）；换模型必须同时改此值
# 与 VECTOR_PG_DIM，缓存命名空间随之变化。
EMBEDDING_DIM = int(os.getenv("VECTOR_PG_DIM", "1024"))


def _request_cache_key(text: str, identity: str) -> str:
    return identity + "|" + hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _embedding_identity(embedding: "_TrackedEmbedding") -> str:
    """embedding 缓存命名空间：provider + 模型 + 维度 + 配置签名。

    配置签名随管理端绑定/凭据版本变化（_embedding_runtime_signature），
    换模型或换维度后旧向量不会命中——与索引侧 INDEX_EMBEDDING_MISMATCH
    的"禁止跨模型复用"同口径。
    """
    try:
        signature = repr(embedding._config_signature)
    except Exception:  # noqa: BLE001 — 签名不可读时退化为模型名
        signature = ""
    return "|".join((
        str(
            getattr(embedding, "_provider_id", "")
            or getattr(embedding, "_provider", "")
            or ""
        ),
        str(getattr(embedding, "_model_name", "") or ""),
        str(EMBEDDING_DIM),
        signature,
    ))


# 进程级命中数（跨请求），供指标与报告读取；请求级统计随请求作用域。
_PROCESS_EMBED_CACHE: "OrderedDict[str, list[float]]" = OrderedDict()

# =====================================================
# Token-tracking wrapper for Embedding calls
# =====================================================

class _TrackedEmbedding(Embeddings):
    """拦截 embed_documents / embed_query，记录 token 用量到 SQLite。"""

    def __init__(self, inner: Embeddings, tracker, *, fixed_binding=None):
        self._inner = inner
        self._tracker = tracker
        self._refresh_lock = threading.RLock()
        self._fixed_binding = fixed_binding
        self._config_signature = _embedding_runtime_signature()
        cloud_enabled = _embedding_is_cloud()
        self._model_name = _metered_embedding_model() if cloud_enabled else EMBEDDING_MODEL_PATH
        self._provider = "cloud" if cloud_enabled else "local"
        self._provider_id = (
            _metered_embedding_provider() if cloud_enabled else "local"
        )
        # 单次 embed_documents 调用的最优文本条数，供索引链路取批大小：
        # cloud 模式受 DashScope 单请求上限约束（外层攒 32 条会被
        # OpenAIEmbeddings 内部再拆 10+10+10+2，白多 3 次 RTT），直接取上限；
        # local 模式批推理走矩阵运算，维持配置批大小。
        if cloud_enabled:
            from backend.config.rag import EMBED_REQUEST_LIMIT
            self.embed_batch_size = max(1, EMBED_REQUEST_LIMIT)
        else:
            from backend.config.rag import EMBED_BATCH_SIZE
            self.embed_batch_size = max(1, EMBED_BATCH_SIZE)
        if fixed_binding is not None:
            self._model_name = fixed_binding.model_name
            self._provider = "cloud"
            self._provider_id = fixed_binding.provider_id
            self._config_signature = (
                "fixed", specialized_mod.embedding_fingerprint(fixed_binding),
            )
        self._set_index_fingerprint()

    def _set_index_fingerprint(self) -> None:
        binding = self._fixed_binding or specialized_mod.resolve_binding("embedding")
        self._index_fingerprint = (
            specialized_mod.embedding_fingerprint(binding)
            if binding and binding.model_name == self._model_name else ""
        )

    def close(self) -> None:
        """短生命周期的预检/迁移客户端用完关闭，单例调用方不调用此方法。"""
        client = getattr(self._inner, "http_client", None)
        if self._fixed_binding is not None and client is not None:
            client.close()

    def _refresh_if_changed(self) -> None:
        """配置轮询后，在下一次调用前替换旧的出站 embedding 客户端。"""
        if self._fixed_binding is not None:
            return
        signature = _embedding_runtime_signature()
        if signature == self._config_signature:
            return
        with self._refresh_lock:
            if signature == self._config_signature:
                return
            cloud_enabled = _embedding_is_cloud()
            self._inner = _get_cloud_embedding() if cloud_enabled else _get_local_embedding()
            self._config_signature = signature
            self._model_name = _metered_embedding_model() if cloud_enabled else EMBEDDING_MODEL_PATH
            self._provider = "cloud" if cloud_enabled else "local"
            self._provider_id = (
                _metered_embedding_provider() if cloud_enabled else "local"
            )
            if cloud_enabled:
                from backend.config.rag import EMBED_REQUEST_LIMIT
                self.embed_batch_size = max(1, EMBED_REQUEST_LIMIT)
            else:
                from backend.config.rag import EMBED_BATCH_SIZE
                self.embed_batch_size = max(1, EMBED_BATCH_SIZE)
            self._set_index_fingerprint()
            logger.info(
                "[Embedding] 检测到配置变化，已热切换客户端 (model=%s)",
                self._model_name,
            )

    # ── 请求级复用底座（R1-A）────────────────────────────────
    # 模型身份在刷新配置后可能变化 → 每次调用前重算（O(1) 字符串拼接）。
    def _identity(self) -> str:
        return _embedding_identity(self)

    def _model_call_scope(self):
        """缓存命中不预占；真正云端调用才进入统一模型预算。"""
        from backend.infra.llm.budget import scoped_model_call

        if self._provider == "cloud":
            return scoped_model_call(self._model_name, "embedding")
        return nullcontext()

    def _process_cached(self, key: str) -> list | None:
        if not (_EMBED_PROCESS_CACHE_ENABLED and _EMBED_PROCESS_CACHE_MAX > 0):
            return None
        with _EMBED_LOCKS_GUARD:
            cached = _PROCESS_EMBED_CACHE.get(key)
            if cached is not None:
                _PROCESS_EMBED_CACHE.move_to_end(key)
            return cached

    def _store_process_cache(self, key: str, vector: list) -> None:
        """写入跨请求缓存（只淘汰缓存条目；锁是固定条纹表，永远不淘汰）。"""
        if not (_EMBED_PROCESS_CACHE_ENABLED and _EMBED_PROCESS_CACHE_MAX > 0):
            return
        with _EMBED_LOCKS_GUARD:
            _PROCESS_EMBED_CACHE[key] = vector
            _PROCESS_EMBED_CACHE.move_to_end(key)
            while len(_PROCESS_EMBED_CACHE) > _EMBED_PROCESS_CACHE_MAX:
                _PROCESS_EMBED_CACHE.popitem(last=False)

    @staticmethod
    def _bump_field(stats: "EmbeddingRequestStats | None", field_name: str) -> None:
        """按字段累加（requested / computed / request_hits / process_hits）。"""
        if stats is None:
            return
        setattr(stats, field_name, getattr(stats, field_name) + 1)

    @staticmethod
    def _bump_kind(stats: "EmbeddingRequestStats | None", kind: str) -> None:
        """按 kind 累加。

        必须与 _bump_field 分开：早期实现把两者绑在一个 _bump 里，调用点
        既 _bump_field("requested") 又 _bump(..., "computed") 时 kinds 被
        重复累加（实测 kinds=query:4 而 requested=2），指标口径直接失真。
        """
        if stats is None:
            return
        stats.kinds[kind] = stats.kinds.get(kind, 0) + 1

    def _emit_cache_hit(self, hit_source: str) -> None:
        try:
            from backend.observability.metrics import rag_embedding_cache_total

            rag_embedding_cache_total.labels(hit_source=hit_source).inc()
        except Exception:  # noqa: BLE001 — 指标旁路软失败
            pass

    def _emit_call(self, kind: str, duration_ms: float, status: str = "success") -> None:
        """出站调用记账：成功与失败都计数（P1-A：失败不能隐形）。"""
        try:
            from backend.observability.metrics import (
                rag_embedding_call_errors_total,
                rag_embedding_calls_total,
                rag_embedding_duration_seconds,
            )

            rag_embedding_calls_total.labels(
                kind=kind, provider=str(self._provider or "unknown")).inc()
            rag_embedding_duration_seconds.labels(kind=kind).observe(
                max(float(duration_ms), 0.0) / 1000)
            if status != "success":
                rag_embedding_call_errors_total.labels(kind=kind, status=status).inc()
        except Exception:  # noqa: BLE001 — 指标旁路软失败
            pass

    def _cached_embed(
        self,
        key: str,
        kind: str,
        compute: Callable[[], Any],
        to_vector: Callable[[Any], list],
        record_error: Callable[[float], None] | None = None,
        record_success: Callable[[float], None] | None = None,
    ) -> tuple[Any, bool]:
        """单 key 单飞 + 双层缓存。返回 (provider 原始结果, 是否命中缓存)。

        单飞语义（P1-C 修复后）：同 key 并发只有一个线程真正出站，其余在
        **固定条纹锁**上等待后复用其结果——同一 key 恒映射同一把锁，不存在
        "锁被淘汰后建第二把锁"的窗口；等待者复用同一结果，且不重复 compute
        （与进程缓存开关无关：请求级作用域里已有结果）。

        失败语义：异常不入缓存，持锁者在 finally 清掉在途登记，等待者若发现
        在途已消失且缓存仍无值，会自行获取锁重试——即"失败即解除"，不会因为
        别人的失败而永久拿不到结果。
        """
        # P1-B：线程池里 contextvar 为空 → 自建作用域并登记进请求总账，
        # 计数与去重在同一层生效，父请求 end 时拿到的是跨线程完整汇总。
        stats = _embedding_scope()
        self._bump_field(stats, "requested"); self._bump_kind(stats, kind)

        cached = stats.values.get(key)
        if cached is not None:
            self._bump_field(stats, "request_hits")
            stats.values[key] = cached
            stats.vector_dim = stats.vector_dim or len(cached)
            self._emit_cache_hit("request")
            _flush_owned_scope()
            return cached, True

        cached = self._process_cached(key)
        if cached is not None:
            stats.values[key] = cached
            stats.hit_source = "process"
            stats.vector_dim = stats.vector_dim or len(cached)
            self._bump_field(stats, "process_hits")
            self._emit_cache_hit("process")
            _flush_owned_scope()
            return cached, True

        lock = _stripe_lock(key)
        with lock:
            # double-check：等待期间其他线程可能已经算完同一向量
            cached = stats.values.get(key)
            if cached is not None:
                self._bump_field(stats, "request_hits")
                stats.vector_dim = stats.vector_dim or len(cached)
                self._emit_cache_hit("request")
                _flush_owned_scope()
                return cached, True
            cached = self._process_cached(key)
            if cached is not None:
                stats.values[key] = cached
                stats.hit_source = "process"
                stats.vector_dim = stats.vector_dim or len(cached)
                self._bump_field(stats, "process_hits")
                self._emit_cache_hit("process")
                _flush_owned_scope()
                return cached, True

            with _EMBED_LOCKS_GUARD:
                _EMBED_INFLIGHT.setdefault(key, stats)
            t0 = time.monotonic()
            provider_attempted = False
            try:
                with self._model_call_scope():
                    provider_attempted = True
                    try:
                        raw = compute()
                        vector = to_vector(raw)
                    except Exception:
                        if record_error is not None:
                            record_error((time.monotonic() - t0) * 1000)
                        raise
                    if record_success is not None:
                        record_success((time.monotonic() - t0) * 1000)
            except Exception as exc:
                # 失败：不入缓存、记录错误出站（P1-A），并解除在途让等待者重试。
                # computed 也计失败——它表示"真实出站次数"，与 provider 对账用；
                # 只在 computed 里数成功会让"调用数"与真实 RTT 数对不上。
                elapsed_ms = (time.monotonic() - t0) * 1000
                stats.duration_ms += elapsed_ms
                if provider_attempted:
                    self._bump_field(stats, "computed")
                try:
                    if provider_attempted:
                        self._emit_call(kind, elapsed_ms, status="error")
                finally:
                    with _EMBED_LOCKS_GUARD:
                        if _EMBED_INFLIGHT.get(key) is stats:
                            _EMBED_INFLIGHT.pop(key, None)
                    _flush_owned_scope()
                raise exc
            elapsed_ms = (time.monotonic() - t0) * 1000
            with _EMBED_LOCKS_GUARD:
                if _EMBED_INFLIGHT.get(key) is stats:
                    _EMBED_INFLIGHT.pop(key, None)
            stats.values[key] = vector
            stats.vector_dim = len(vector)
            stats.duration_ms += elapsed_ms
            self._bump_field(stats, "computed")
            self._emit_call(kind, elapsed_ms)
            self._store_process_cache(key, vector)
            _flush_owned_scope()
            return raw, False

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量向量化，真实云端调用经过统一预占与结算。"""
        self._refresh_if_changed()
        if not texts:
            return []
        return self._embed_documents(texts)

    def _embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量向量化（索引主路径）。

        R1-A 只对检索期的 query 向量做请求级复用：embed_documents 是离线/
        索引路径，一次调用的入参天然去重。云端按 inner 客户端真实 chunk_size
        显式拆批，每个 Provider 子批次分别预占并结算，避免内层自动拆批少记调用。
        """
        if self._provider == "cloud":
            # 当前云端客户端是 OpenAIEmbeddings；chunk_size 是它实际使用的
            # HTTP 请求批大小。未知客户端退化为单条，确保每次 scope 至多
            # 包含一个输入，不把未声明的内部拆批算成一次预算调用。
            try:
                provider_batch_size = max(1, int(getattr(self._inner, "chunk_size", 1)))
            except (TypeError, ValueError):
                provider_batch_size = 1
        else:
            provider_batch_size = max(1, len(texts))

        result: list[list[float]] = []
        for start in range(0, len(texts), provider_batch_size):
            batch = texts[start:start + provider_batch_size]
            t0 = time.monotonic()
            with self._model_call_scope():
                try:
                    batch_result = self._inner.embed_documents(batch)
                except Exception:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    # 失败可能已由供应商处理；不虚构 token，转待对账。
                    self._record(None, len(batch), elapsed_ms, status="error")
                    self._emit_call("documents", elapsed_ms, status="error")
                    self._account_documents(len(batch), elapsed_ms)
                    raise

                elapsed_ms = (time.monotonic() - t0) * 1000
                total_tokens = self._estimate_tokens(batch)
                self._record(total_tokens, len(batch), elapsed_ms)
                self._emit_call("documents", elapsed_ms)
                self._account_documents(len(batch), elapsed_ms)
                result.extend(batch_result)
        return result

    def _account_documents(self, count: int, elapsed_ms: float) -> None:
        """把一次批量出站计入请求总账（成功/失败都计，出站次数口径）。"""
        stats = _embedding_scope()
        stats.requested += count
        stats.computed += count
        stats.duration_ms += elapsed_ms
        stats.kinds["documents"] = stats.kinds.get("documents", 0) + count
        _flush_owned_scope()

    def embed_query(self, text: str) -> List[float]:
        """单条查询向量化（检索主路径；R1-A 去重 + 单飞 + 双层缓存）。"""
        self._refresh_if_changed()
        key = _request_cache_key(text, self._identity())

        def _compute() -> Any:
            return self._inner.embed_query(text)

        def _to_vector(raw: Any) -> list:
            # 只做形状校验与转换，不落账：转换失败必须走失败路径，
            # 否则会先写成功行、再写失败行（同一次出站双记录）。
            if raw is None:
                raise ValueError("embedding provider 返回空结果")
            if isinstance(raw, (bytes, bytearray, str, dict)):
                raise ValueError(
                    f"embedding provider 返回非法类型 {type(raw).__name__}")
            try:
                vector = [float(v) for v in raw]
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"embedding provider 返回无法转为数值向量: {exc}") from exc
            if not vector:
                raise ValueError("embedding provider 返回空向量")
            return vector

        def _record_success(elapsed_ms: float) -> None:
            """成功用量行：token 为估算值（provider 未回传真实 usage 时的既有口径）。"""
            total_tokens = self._estimate_tokens([text])
            self._record(total_tokens, 1, elapsed_ms)

        def _record_error(elapsed_ms: float) -> None:
            """出站失败/超时的用量审计（status=error、token 记 0）。

            计量粒度（与 GPT 审查口径一致）：这里记的是**外层一次调用**。
            inner 客户端（openai SDK）自带 1 次重试，真实 HTTP 尝试次数可能
            大于 1；SDK 不逐次回传 usage，因此账本按"调用"计而非按"HTTP 尝试"
            计——失败行的 token 恒为 0/UNKNOWN，不虚构消费。
            """
            self._record(None, 1, elapsed_ms, status="error")

        return self._cached_embed(
            key, "query", _compute, _to_vector, _record_error, _record_success)[0]

    @staticmethod
    def _estimate_tokens(texts: List[str]) -> int:
        """粗估 token 数：中文 ~1.5 chars/token，英文 ~4 chars/token。"""
        total_chars = sum(len(t) for t in texts)
        # 保守估计：平均 3 chars/token
        return max(1, total_chars // 3)

    def _record(self, total_tokens, doc_count, duration_ms, status="success"):
        """写入统一用量账本，并使用同一计费结果结算预算。

        2026-09-22 起成本与 token 计量连通：云端调用按 `model_price` 实价
        计费（供应商页登记即生效），enforce=False 时缺价自动回落旧估算，
        绝不因计费故障丢用量行。
        """
        billing = None
        try:
            from backend.observability.llm_usage_store import (
                current_usage_attribution,
                get_llm_usage_store,
            )
            # Billing V2（2026-10-07 收口）：唯一计费入口，行金额恒 CNY 记账；
            # 原生成本/币种/汇率/价格版本随行走 V2 列（cost_usd 只在原生 USD
            # 时有值，杜绝 CNY 值写进 USD 列再被看板二次换汇）。
            if self._provider == "cloud" and (total_tokens or 0) > 0:
                try:
                    from backend.infra.llm.pricing import (
                        NormalizedUsage,
                        price_usage,
                    )

                    billing = price_usage(
                        self._model_name,
                        "embedding",
                        NormalizedUsage(input_tokens=total_tokens or 0),
                        enforce=False,
                        usage_source="estimated",
                    )
                except Exception as e:
                    from backend.shared.logger import logger as _logger
                    _logger.debug(
                        "[Embedding] 用量计费失败（用量行照常记录，成本记 0）: %s", e,
                    )
            attribution = current_usage_attribution()
            event = {
                "component": "embedding",
                "model": self._model_name,
                "provider": getattr(self, "_provider_id", None) or self._provider,
                "prompt_tokens": total_tokens or 0,
                "completion_tokens": 0,
                "total_tokens": total_tokens or 0,
                "duration_ms": duration_ms,
                "trace_id": attribution["trace_id"],
                "session_id": attribution["session_id"],
                "request_id": attribution["request_id"],
                "user_id": attribution["user_id"],
                "tenant_id": attribution["tenant_id"],
                "run_id": attribution["run_id"],
                "step_id": attribution["step_id"],
                "role": attribution["role"] or "embedding",
                "stage": attribution["stage"] or "embedding",
                "finish_reason": status,
            }
            if billing is not None:
                event.update({
                    "billing_schema_version": 2,
                    "native_cost": float(billing.native_cost),
                    "native_currency": billing.native_currency,
                    "billed_cost_cny": float(billing.billed_cost_cny),
                    "total_cost": float(billing.billed_cost_cny),
                    "fx_rate": (float(billing.fx_rate)
                                if billing.fx_rate is not None else None),
                    "price_version": billing.price_version or "",
                    "pricing_source": billing.pricing_source,
                    "usage_source": billing.usage_source,
                    "input_cost": float(billing.input_cost_cny),
                    "cost_status": billing.cost_status,
                    "cost_usd": (float(billing.native_cost)
                                 if billing.native_currency == "USD" else 0.0),
                    "currency": billing.native_currency,
                })
            else:
                event.update({
                    "cost_usd": 0.0,
                    "total_cost": 0.0,
                    "cost_status": "unpriced",
                })
            get_llm_usage_store().record(event)
        except Exception:
            pass  # 软失败
        finally:
            if self._provider == "cloud":
                from backend.infra.llm.budget import record_model_usage

                priced = billing is not None and billing.cost_status in {
                    "exact", "estimated",
                }
                record_model_usage(
                    prompt_tokens=total_tokens,
                    total_tokens=total_tokens,
                    cost=billing.billed_cost_cny if priced else None,
                    review_reason=(
                        "" if status == "success" and priced
                        else "embedding_usage_or_price_unavailable"
                    ),
                )


# =====================================================
# Singleton with Token Tracker
# =====================================================

_embedding: Embeddings | None = None
_lock = threading.Lock()
_tracker = None


def _init_tracker():
    """懒加载 TokenTracker."""
    global _tracker
    if _tracker is None:
        cloud_enabled = (
            EMBEDDING_PROVIDER == "cloud"
            or specialized_mod.resolve_binding("embedding") is not None
        )
        _tracker = create_tracker_for_embedding(
            log_path=str(Path(TOKEN_USAGE_LOG_PATH).expanduser().resolve()),
            model_name=_metered_embedding_model() if cloud_enabled else EMBEDDING_MODEL_PATH,
            backend=EMBEDDING_PROVIDER,
        )
    return _tracker


def get_registered_embedding(
    model_name: str, *, request_timeout: float | None = None,
    batch_size: int | None = None, max_retries: int = 1,
) -> Embeddings:
    """迁移/预检复用正式客户端和用量链路，本次配置固定不随角色热切换。"""
    from backend.tools.url_guard import assert_url_allowed
    from backend.infra.llm import models as models_mod

    binding = specialized_mod.registered_binding("embedding", model_name)
    provider = models_mod.get_provider_entry(binding.provider_id) or {}
    assert_url_allowed(
        binding.base_url, allow_private=provider.get("network_scope") == "private",
    )
    credentials = credentials_mod.resolve_credentials(
        binding.provider_id, model_name=model_name,
    )
    inner = _get_cloud_embedding({
        "model": binding.upstream_name or model_name,
        "api_key": credentials.api_key or "", "base_url": binding.base_url,
        "dimensions": binding.options.get("dimensions"),
        "provider": binding.provider_id,
        "network_scope": provider.get("network_scope", "public"),
    }, request_timeout=request_timeout, batch_size=batch_size,
        max_retries=max_retries)
    tracker = create_tracker_for_embedding(
        log_path=str(Path(TOKEN_USAGE_LOG_PATH).expanduser().resolve()),
        model_name=model_name, backend="cloud",
    )
    return _TrackedEmbedding(inner, tracker, fixed_binding=binding)


def get_embedding() -> Embeddings:
    """获取共享的 embedding 模型实例（线程安全单例）。

    根据 EMBEDDING_PROVIDER 自动选择 Cloud/Local 后端:
      - EMBEDDING_PROVIDER=cloud → OpenAIEmbeddings（DashScope/SiliconFlow 等）
      - EMBEDDING_PROVIDER=local → HuggingFaceEmbeddings (BGE)
    留空 EMBEDDING_PROVIDER 时跟随 ENV_MODE（向后兼容）。

    返回：
        Embeddings: LangChain Embeddings 接口

    抛出:
        RuntimeError: Cloud 模式下数据库未绑定 embedding 或供应商缺少 API Key
    """
    global _embedding

    if _embedding is None:
        with _lock:
            if _embedding is None:
                # 根据 EMBEDDING_PROVIDER 选择后端
                if (
                    EMBEDDING_PROVIDER == "cloud"
                    or specialized_mod.resolve_binding("embedding") is not None
                ):
                    base_embedding = _get_cloud_embedding()
                else:
                    base_embedding = _get_local_embedding()

                # 包装为带 Token Tracker 的版本
                tracker = _init_tracker()
                _embedding = _TrackedEmbedding(base_embedding, tracker)

                logger.info(
                    f"[Embedding] 全局单例创建完成 "
                    f"(provider={EMBEDDING_PROVIDER}, env_mode={ENV_MODE}, tracking=True)"
                )
    
    return _embedding


def reset_embedding():
    """重置单例（仅用于测试）."""
    global _embedding, _tracker
    with _lock:
        _embedding = None
        _tracker = None
        logger.warning("[Embedding] 单例已重置（测试用）")
