"""Reranker Module - CrossEncoder + Cloud API Integration

P0 架构重构 (双模式):
1. RERANK_PROVIDER=cloud → Cloud Reranker (DashScope qwen3-rerank / SiliconFlow bge-reranker)
2. RERANK_PROVIDER=local → Local Reranker (BGE CrossEncoder)
3. RERANK_MODEL 配置动态化 (默认 qwen3-rerank)
4. Token Tracker 记录用量，Local 模式无法获取 usage 时 total_tokens=null

RERANK_PROVIDER 留空时跟随 ENV_MODE（向后兼容）。

架构特性:
- 懒加载：本地模型仅在首次调用时加载
- 强制模式：根据 RERANK_PROVIDER 选择后端，不自动降级
- 统一接口：返回值始终为 list[tuple[Document, float]]
- 可观测性：完整日志追踪 backend_type、评分详情

配置环境变量:
- RERANK_PROVIDER: "cloud" | "local"（留空跟随 ENV_MODE）
- ENV_MODE: "cloud" (默认) | "local"（全局兜底）
- RERANK_MODEL: "qwen3-rerank" (Cloud 模式)
- RERANK_API_FORMAT: "dashscope" | "jina"（Cloud 模式协议）
- RERANKER_MODEL_PATH: 本地 CrossEncoder 模型路径 (Local 模式)
- DASHSCOPE_API_KEY / RERANK_API_KEY: Cloud 模式必需
- RERANK_TIMEOUT: API 超时阈值 (秒)，默认 5
- RERANK_TOP_K: 返回文档数，默认 8
- RERANK_SCORE_THRESHOLD: 分数过滤阈值，默认 0.3
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
import math
import re
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

import requests

# dashscope SDK 可选保留（仅用于错误类型兼容）
try:
    import dashscope
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False

# sentence_transformers 延迟导入：顶层 import 连带 torch/transformers（~8s），
# 仅 ENV_MODE=local 真正加载本地 CrossEncoder 时才需要（LazyLocalModelLoader）。
if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

from langchain_core.documents.compressor import BaseDocumentCompressor
from backend.config import (
    ENV_MODE,
    RERANK_PROVIDER,
    RERANK_MODEL,
    RERANK_API_FORMAT,
    RERANK_BASE_URL,
    RERANKER_MODEL_PATH,
    RERANK_SCORE_THRESHOLD,
    RERANK_TIMEOUT,
    RERANK_TOP_K,
    RERANKER_DEVICE,
    TOKEN_USAGE_LOG_PATH,
)
from backend.config import model_roles
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import specialized as specialized_mod
from backend.infra.token_tracker import TokenTracker, create_tracker_for_rerank
from pathlib import Path
from backend.shared.logger import logger


def _configured_rerank_model() -> str:
    """读取重排角色；无 DB 覆盖时保持历史模块常量语义。"""
    return model_roles.resolve_runtime_name("rerank", RERANK_MODEL)


def _resolve_rerank_runtime_config() -> dict[str, str]:
    """解析专项 DB 绑定；未配置时返回未配置状态，不读取旧 env。"""
    binding = specialized_mod.resolve_binding("rerank")
    if binding is None:
        return {
            "model": _configured_rerank_model(),
            "billing_model": _configured_rerank_model(),
            "api_key": "",
            "api_format": RERANK_API_FORMAT,
            "base_url": "",
            "provider": "unknown",
        }

    credentials = credentials_mod.resolve_credentials(
        binding.provider_id,
        model_name=binding.model_name,
    )
    adapter_format = {
        "dashscope_rerank": "dashscope",
        "jina_rerank": "jina",
    }.get(binding.adapter)
    if adapter_format is None:
        raise RuntimeError(f"未知重排适配器：{binding.adapter}")
    return {
        # 登记名/上游名拆分（2026-09-22）：发给厂商 API 的 model 用 upstream。
        # 兼容旧版/测试替身 binding：专项绑定新增 upstream_name 后，
        # 缺少该可选属性仍应回退登记名。
        "model": getattr(binding, "upstream_name", "") or binding.model_name,
        "billing_model": binding.model_name,
        "api_key": credentials.api_key or "",
        "api_format": adapter_format,
        "base_url": binding.base_url,
        "provider": binding.provider_id,
    }


def _reranker_runtime_signature() -> tuple[Any, ...]:
    """返回影响重排客户端的配置签名，供已存在的 RAGPipeline 热切换。"""
    binding = specialized_mod.resolve_binding("rerank")
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
        "unconfigured",
        RERANK_PROVIDER,
        _configured_rerank_model(),
        RERANK_API_FORMAT,
        "",
        False,
    )


# ═══════════════════════════════════════════════════════════
# Local Model Loader - 懒加载单例模式
# ═══════════════════════════════════════════════════════════

class LocalModelLoader:
    """本地 CrossEncoder 模型懒加载单例"""
    _instance: CrossEncoder | None = None
    _loaded_at: str = ""
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> CrossEncoder:
        """获取或创建 CrossEncoder 实例 (线程安全：双检锁，模型加载耗时长，
        并发首查若无锁会重复加载数百 MB 模型)"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    from sentence_transformers import CrossEncoder
                    cls._instance = CrossEncoder(RERANKER_MODEL_PATH, device=RERANKER_DEVICE)
                    cls._loaded_at = __import__('datetime').datetime.now().isoformat()
                    logger.info(f"本地 reranker 模型懒加载完成：{RERANKER_MODEL_PATH} (device={RERANKER_DEVICE}, at {cls._loaded_at})")
        return cls._instance

    @classmethod
    def is_loaded(cls) -> bool:
        """检查模型是否已加载"""
        return cls._instance is not None

    @classmethod
    def reset(cls) -> None:
        """重置实例 (用于测试)"""
        cls._instance = None
        cls._loaded_at = ""


# ═══════════════════════════════════════════════════════════
# Cloud Reranker - API Backend (dashscope | jina 双协议)
# ═══════════════════════════════════════════════════════════

# 两种协议的默认 base / 路径：
#   dashscope → 阿里云百炼原生 REST（qwen3-rerank）
#   jina      → Jina 兼容 /rerank（SiliconFlow 的 bge-reranker-v2-m3 等）
_DEFAULT_RERANK_BASE = {
    "dashscope": "https://dashscope.aliyuncs.com/api/v1",
    "jina": "https://api.siliconflow.cn/v1",
}
_DASHSCOPE_RERANK_PATH = "/services/rerank/text-rerank/text-rerank"
_JINA_RERANK_PATH = "/rerank"

# ── 多查询全候选评分（PR #52，Phase C/D）─────────────────────────────
# Provider 的 top_n 会裁剪返回结果：若每条查询只取 Top-8，未进入前 8 的候选
# 就没有分数，max 聚合会被"缺失即 0"污染。因此多查询路径按候选全量请求
# （top_n = 本批候选数），单次请求覆盖不了时按批切分，每批仍全量评分。
_RERANK_BATCH_SIZE = max(1, int(os.getenv("RERANK_MAX_CANDIDATES", "100")))
# 剩余检索预算低于该阈值时不再发起后续查询的评分（原问题评分不受此限）。
_RERANK_MQ_MIN_BUDGET_MS = max(0.0, float(os.getenv("RERANK_MQ_MIN_BUDGET_MS", "1200")))
# 单次 HTTP 尝试的最短可用预算；不足即视为"不得提交的调用"。
_RERANK_MIN_ATTEMPT_S = 0.2
# 分数合法区间 0-1 的浮点噪声容忍度（只夹紧，不放宽语义）。
_RERANK_SCORE_EPS = 1e-6
# 仅原问题Worker绑定；成功预占并准备发出HTTP后才放行变体预占。
_primary_rerank_dispatch: ContextVar[Any] = ContextVar(
    "primary_rerank_dispatch", default=None,
)
_rerank_query_index: ContextVar[int | None] = ContextVar(
    "rerank_query_index", default=None,
)


class RerankLateResultDiscarded(RuntimeError):
    """Provider 已返回但共享检索 deadline 已过；只结算用量，不采用分数。"""


def _budget_deadline_at() -> float | None:
    """当前请求的检索 deadline（monotonic）；旁路调用/离线评测返回 None。

    多查询评分必须与检索链共享同一份 deadline，不允许每条查询各自重新
    获得完整超时。
    """
    from backend.rag.retrieval_budget import current_budget

    budget = current_budget()
    return None if budget is None else budget.deadline_at


def _bounded_attempt_timeout(
    provider_timeout: float,
    requested_s: float | None,
    deadline_at: float | None,
) -> float:
    """单次 HTTP 尝试可用超时 = min(Provider 超时, 指定超时, 真实剩余预算)。"""
    limit = float(provider_timeout)
    if requested_s is not None:
        limit = min(limit, float(requested_s))
    if deadline_at is not None:
        limit = min(limit, deadline_at - time.monotonic())
    return limit


def _budget_allows_extra_query(
    deadline_at: float | None, min_ms: float | None = None,
) -> bool:
    """剩余预算是否还够发起"额外"查询评分（原问题评分不走此守卫）。"""
    if deadline_at is None:
        return True
    need_s = (min_ms if min_ms is not None else _RERANK_MQ_MIN_BUDGET_MS) / 1000
    return (deadline_at - time.monotonic()) >= need_s


def _rerank_document_text(doc, limit: int = 2000) -> str:
    """Provider 侧授权文本表达：文件名标题 + 正文（截断），不含本机路径。"""
    source = str(doc.metadata.get("title") or doc.metadata.get("source") or "")
    title = source.replace("\\", "/").rsplit("/", 1)[-1][:160]
    text = f"文档：{title}\n{doc.page_content}" if title else doc.page_content
    return text[:limit]


def full_candidate_scores(
    backend, query: str, documents: list[str], deadline_at: float | None = None,
) -> list[tuple[int, float]]:
    """对同一批候选取得全部（不裁剪）评分；后端不支持时抛 TypeError。

    不把缺失项伪造成 0/1 分：批内请求失败直接抛出，由调用方把该查询标记为
    失败；成功的批次也会完整返回本批全部候选的分数。
    """
    rank_all = getattr(backend, "rank_all", None)
    if callable(rank_all):
        return list(rank_all(query, documents, deadline_at=deadline_at))
    rank = getattr(backend, "rank", None)
    if callable(rank):
        return list(rank(query, documents, top_k=len(documents)))
    raise TypeError("rerank 后端不支持全候选评分")


def _backend_rerank_text(backend, doc) -> str:
    """按后端既有授权文本表达构造评分文本（多查询路径与单查询保持一致）。"""
    builder = getattr(backend, "rerank_text", None)
    if callable(builder):
        return builder(doc)
    return (doc.page_content or "")[:2000]


def _resolve_rerank_base() -> str:
    """兼容入口：运行时地址必须来自数据库专项绑定。"""
    return ""

class DashScopeReranker(BaseDocumentCompressor):
    """云端 Reranker API 实现（直接 HTTP，无需 dashscope SDK）

    支持两种协议（RERANK_API_FORMAT env 切换）：
    - dashscope: {"model","input":{...},"parameters":{...}} → output.results
    - jina:      {"model","query","documents","top_n"}      → results（扁平结构）

    需要标准 API Key（sk-ws-）；Token Plan（sk-sp-）不支持 rerank 端点。

    P0: 模型名从 RERANK_MODEL 配置读取，不再硬编码
    """

    def __init__(
        self,
        api_key: str,
        timeout: int = 5,
        *,
        model: str | None = None,
        api_format: str | None = None,
        base_url: str | None = None,
        provider_id: str | None = None,
        billing_model: str | None = None,
    ):
        if not api_key:
            raise RuntimeError("云端 Reranker 需要在管理端数据库配置供应商 API Key")

        resolved_format = api_format or RERANK_API_FORMAT
        resolved_model = model or _configured_rerank_model()
        resolved_base_url = (base_url or _resolve_rerank_base()).rstrip("/")
        endpoint = resolved_base_url + (
            _DASHSCOPE_RERANK_PATH if resolved_format == "dashscope" else _JINA_RERANK_PATH
        )
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        self.__dict__['api_key'] = api_key
        self.__dict__['timeout'] = timeout
        self.__dict__['_endpoint'] = endpoint
        self.__dict__['_headers'] = headers
        self.__dict__['_model'] = resolved_model
        self.__dict__['_provider_id'] = provider_id or "unknown"
        self.__dict__['_billing_model'] = billing_model or resolved_model
        self.__dict__['_api_format'] = resolved_format

        logger.info(
            f"初始化 Cloud Reranker HTTP "
            f"(format={resolved_format}, model={resolved_model}, endpoint={resolved_base_url}, timeout={timeout}s)"
        )

    def rank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 8,
        *,
        timeout_s: float | None = None,
        deadline_at: float | None = None,
    ) -> list[tuple[int, float]]:
        """调用 DashScope Rerank REST API。

        Args:
            top_k: 返回条数（Provider 的 top_n）。全候选评分由 rank_all 传
                len(documents)，避免裁剪造成分数缺失。
            timeout_s: 调用方指定的单次尝试超时（秒）。
            deadline_at: 共享检索 deadline（monotonic 秒）；每次尝试与重试
                都不得超过真实剩余时间。

        Returns:
            list[tuple[int, float]]: (index, relevance_score) 列表，score 已在 0-1 区间
        """
        if not documents:
            return []
        if top_k <= 0:
            return []
        if deadline_at is None:
            deadline_at = _budget_deadline_at()
        if self._api_format == "jina":
            # Jina 兼容格式（SiliconFlow / TEI / jina rerank）
            payload = {
                "model": self._model,
                "query": query,
                "documents": documents,
                "top_n": top_k,
            }
        else:
            # DashScope 原生格式
            payload = {
                "model": self._model,
                "input": {
                    "query": query,
                    "documents": documents,
                },
                "parameters": {
                    "top_n": top_k,
                    "return_documents": False,
                },
            }

        from backend.rag.context import get_context

        request_state = get_context()
        provider_key = f"{self._provider_id}|{self._endpoint}|{self._model}"
        if provider_key in request_state.rerank_unavailable:
            raise requests.exceptions.Timeout("本请求的重排服务已超时，跳过重复投递")
        attempt_timeout = _bounded_attempt_timeout(
            self.timeout, timeout_s, deadline_at,
        )
        if attempt_timeout < _RERANK_MIN_ATTEMPT_S:
            raise requests.exceptions.Timeout("rerank 剩余检索预算不足，未发起 HTTP 尝试")
        try:
            return self._rank_once(
                payload, len(documents), "primary",
                timeout=attempt_timeout, deadline_at=deadline_at,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            # 客户端超时不代表供应商已停算；同轮纠错不得再叠加相同推理。
            request_state.rerank_unavailable.add(provider_key)
            raise

    def rank_all(
        self,
        query: str,
        documents: list[str],
        *,
        deadline_at: float | None = None,
    ) -> list[tuple[int, float]]:
        """对全部候选评分（不裁剪），必要时按批切分。

        批内 top_n = 本批候选数，保证每个候选都拿到自己的真实分数；批间
        索引回填为全局索引，绝不静默丢候选。
        """
        scored: list[tuple[int, float]] = []
        total = len(documents)
        for start in range(0, total, _RERANK_BATCH_SIZE):
            batch = documents[start:start + _RERANK_BATCH_SIZE]
            rows = self.rank(
                query, batch, top_k=len(batch), deadline_at=deadline_at,
            )
            for local_idx, score in rows:
                scored.append((start + local_idx, score))
        return scored

    def _rank_once(
        self, payload, doc_count: int, kind: str,
        timeout: float | None = None, deadline_at: float | None = None,
    ):
        """每次 HTTP 尝试独立预占、Trace 与记账；迟到分数不得回流业务。"""
        from backend.infra.llm.budget import (
            current_request_id,
            current_reservation_id,
            scoped_model_call,
        )
        from backend.observability.tracer import (
            current_trace_context,
            trace_collector,
        )

        attempt_id = uuid.uuid4().hex
        trace_id, _ = current_trace_context()
        started = time.monotonic()
        metrics = {
            "internal_call_id": attempt_id,
            "provider_attempt_id": attempt_id,
            "role": "rerank",
            "query_index": _rerank_query_index.get(),
            "model": self._billing_model,
            "provider": self._provider_id,
            "request_id": current_request_id() or "",
            "trace_id": trace_id or "",
            "provider_ttfb_ms": "unavailable",
            "provider_call_started": False,
            "provider_completed": False,
            "provider_cancel_acknowledged": False,
            "usage_status": "pending",
            "settlement_status": "pending",
            "timeout_kind": "none",
        }
        response_received = False
        usage_recorded = False
        with scoped_model_call(self._billing_model, "rerank", kind=kind):
            # 价格校验和预占也会耗时，HTTP必须使用此刻的真实剩余预算。
            timeout = _bounded_attempt_timeout(self.timeout, timeout, deadline_at)
            if timeout < _RERANK_MIN_ATTEMPT_S:
                from backend.infra.llm.budget import release_model_reservation

                # 可以证明尚未发给Provider，才能释放；不写伪造的零usage。
                release_model_reservation()
                raise requests.exceptions.Timeout("rerank 预占后预算不足，未发起HTTP")
            metrics["reservation_id"] = current_reservation_id() or ""
            metrics["reservation_kind"] = kind
            span = trace_collector.start_span(
                "rerank_provider_attempt",
                name="Rerank Provider 尝试",
                type="llm_call",
            )
            try:
                on_dispatch = _primary_rerank_dispatch.get()
                if on_dispatch is not None:
                    on_dispatch()
                metrics["provider_call_started"] = True
                provider_started = time.monotonic()
                resp = requests.post(
                    self._endpoint, json=payload, headers=self._headers,
                    timeout=self.timeout if timeout is None else timeout,
                )
                provider_elapsed = (time.monotonic() - provider_started) * 1000
                metrics["provider_call_ms"] = round(provider_elapsed, 1)
                metrics["provider_response_ms"] = round(provider_elapsed, 1)
                metrics["provider_completed"] = True
                response_received = True
                try:
                    elapsed = resp.elapsed.total_seconds() * 1000
                    if elapsed >= 0:
                        metrics["provider_ttfb_ms"] = round(elapsed, 1)
                except (AttributeError, TypeError, ValueError):
                    metrics["provider_ttfb_ms"] = "unavailable"
                headers = getattr(resp, "headers", {}) or {}
                for header in ("x-request-id", "request-id", "x-dashscope-request-id"):
                    candidate = str(headers.get(header, "") or "")[:128]
                    if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate):
                        metrics["provider_request_id"] = candidate
                        break
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Rerank provider returned HTTP {resp.status_code}"
                    )
                data = resp.json()
                results = (
                    data.get("output", {}).get("results")
                    if self._api_format == "dashscope" else data.get("results")
                ) or []
                scored = [(row["index"], row["relevance_score"]) for row in results]
                raw_tokens = data.get("usage", {}).get("total_tokens", 0)
                try:
                    tokens = max(0, int(raw_tokens))
                except (ValueError, TypeError):
                    tokens = 0
                parse_ms = (time.monotonic() - provider_started) * 1000
                metrics["response_parse_ms"] = round(
                    max(parse_ms - provider_elapsed, 0), 1,
                )
                accounting = self._record_tokens(
                    doc_count, (time.monotonic() - started) * 1000,
                    total_tokens=tokens,
                )
                usage_recorded = True
                if isinstance(accounting, dict):
                    metrics.update(accounting)
                else:
                    metrics.update(
                        usage_status="received" if tokens > 0 else "unavailable",
                        settlement_status=(
                            "settlement_attempted" if tokens > 0 else "needs_review"
                        ),
                        usage_ledger_status="unavailable",
                    )
                if deadline_at is not None and time.monotonic() >= deadline_at:
                    metrics.update(
                        timeout_kind="total_deadline_expired",
                        provider_completed_late=True,
                        caller_outcome="timeout_or_degraded",
                        late_result_discarded=True,
                    )
                    metrics["attempt_total_ms"] = round(
                        (time.monotonic() - started) * 1000, 1,
                    )
                    trace_collector.end_span(
                        span, metrics=metrics, status="completed_late",
                    )
                    raise RerankLateResultDiscarded(
                        "Rerank response arrived after shared retrieval deadline"
                    )
                metrics["attempt_total_ms"] = round(
                    (time.monotonic() - started) * 1000, 1,
                )
                trace_collector.end_span(span, metrics=metrics, status="success")
                return scored
            except RerankLateResultDiscarded:
                raise
            except Exception as exc:
                if not usage_recorded:
                    accounting = self._record_tokens(
                        doc_count, (time.monotonic() - started) * 1000,
                        status="error",
                    )
                    if isinstance(accounting, dict):
                        metrics.update(accounting)
                    else:
                        metrics.update(
                            usage_status="unavailable",
                            settlement_status="needs_review",
                            usage_ledger_status="unavailable",
                        )
                timeout_error = (
                    isinstance(exc, requests.exceptions.Timeout)
                    or "timeout" in type(exc).__name__.lower()
                )
                if "provider_started" in locals() and not response_received:
                    metrics["provider_call_ms"] = round(
                        (time.monotonic() - provider_started) * 1000, 1,
                    )
                    metrics["provider_response_ms"] = "unavailable"
                metrics.update(
                    usage_status="unavailable",
                    settlement_status="needs_review",
                    provider_completion=(
                        "unknown" if not response_received else "response_received"
                    ),
                    provider_error_type=type(exc).__name__,
                    timeout_kind=(
                        "total_deadline_expired"
                        if deadline_at is not None and time.monotonic() >= deadline_at
                        else "provider_io_timeout" if timeout_error else "none"
                    ),
                    attempt_total_ms=round(
                        (time.monotonic() - started) * 1000, 1,
                    ),
                )
                trace_collector.end_span(span, metrics=metrics, status="error")
                raise

    def rerank_text(self, doc, limit: int = 2000) -> str:
        """单条候选的授权评分文本（文件名标题 + 正文），不含本机目录。"""
        return _rerank_document_text(doc, limit=limit)

    def compress_documents(self, documents, query, **kwargs):
        """BaseDocumentCompressor 接口实现

        span 埋点在外层 RerankCompressor.compress_documents（同名 "rerank"），
        此处不再重复创建 —— 旧双层埋点导致 trace 树出现两条几乎等时的 rerank 行。
        """
        if not documents:
            return []

        try:
            # 重排需要文档来源语义，不能把政策正文与 FAQ 摘要当成无标题片段。
            # 仅使用已授权候选的标题/文件名，不向 Provider 发送本机目录。
            texts = [self.rerank_text(doc) for doc in documents]
            ranked_results = self.rank(query, texts, top_k=kwargs.get("top_k", RERANK_TOP_K))

            # 应用阈值过滤并限制数量
            threshold = kwargs.get("threshold", RERANK_SCORE_THRESHOLD)
            top_k = kwargs.get("top_k", RERANK_TOP_K)

            result = [
                (documents[idx], score)
                for idx, score in ranked_results
                if score > threshold
            ][:top_k]

            # 写入 rerank_score 到 metadata
            for doc, score in result:
                doc.metadata["rerank_score"] = round(float(score), 4)

            # 2026-10-29 评测诊断：拒答边界抖动（023 类排序波动）需区分
            # 「精确行未进重排池」与「进了但被阈值截断」，只记 id/分数。
            import os as _os

            if _os.getenv("EVAL_TRIGGER") == "prompt_publish":
                _cut = [
                    (documents[idx].metadata.get("chunk_id", "?"), round(float(s), 4))
                    for idx, s in ranked_results if s <= threshold
                ][:10]
                logger.info(
                    "[RerankDebug] q=%r in=%d kept=%d threshold=%s cut_top=%s",
                    str(query)[:40], len(documents), len(result),
                    threshold, _cut,
                )

            return [doc for doc, _ in result]

        except Exception as e:
            logger.error(f"DashScope API rerank 失败：{e}")
            raise

    def _record_tokens(self, doc_count, duration_ms, total_tokens=0, status="success"):
        """写入统一用量账本，并使用同一计费结果结算预算。

        2026-09-22 起成本与 token 计量连通：按 `model_price` 实价计费
        （供应商页登记即生效），enforce=False 时缺价回落旧估算，不丢用量行。
        2026-10-28：重排 API 不回 token 时按候选文档数估算（与本地
        CrossEncoder 路径同口径，usage_source=estimated）——此前 total=0
        直接跳过 price_usage，本地重排（billing=local）全部落 unpriced。
        """
        billing = None
        ledger_written = False
        settlement_status = "unknown"
        usage_estimated = (total_tokens or 0) <= 0 and doc_count > 0
        if usage_estimated:
            # 估算口径与本地路径一致：平均每文档 ~500 chars ÷ 3 chars/token。
            total_tokens = doc_count * 150
        try:
            from backend.infra.llm.budget import current_call_decision
            from backend.observability.llm_usage_store import (
                current_usage_attribution,
                get_llm_usage_store,
            )
            from backend.observability.tracer import current_trace_context
            trace_id, session_id = current_trace_context()
            # Billing V2（2026-10-07 收口）：唯一计费入口，行金额恒 CNY 记账，
            # 原生成本/币种随行走 V2 列（此前 CNY 值写 cost_usd 且 currency 空，
            # 会被按 USD 二次换汇）。
            if (total_tokens or 0) > 0:
                try:
                    from backend.infra.llm.pricing import (
                        USAGE_SOURCE_ESTIMATED,
                        USAGE_SOURCE_PROVIDER,
                        NormalizedUsage,
                        price_usage,
                    )

                    billing = price_usage(
                        self._billing_model,
                        "rerank",
                        NormalizedUsage(input_tokens=total_tokens),
                        enforce=False,
                        usage_source=(
                            USAGE_SOURCE_ESTIMATED if usage_estimated
                            else USAGE_SOURCE_PROVIDER
                        ),
                    )
                except Exception as e:
                    logger.debug(
                        "[Reranker] 用量计费失败（保留 unpriced 状态）: %s", e,
                    )
            event = {
                "component": "rerank",
                "model": self._billing_model,
                "provider": self._provider_id,
                "prompt_tokens": total_tokens,
                "completion_tokens": 0,
                "total_tokens": total_tokens,
                "duration_ms": duration_ms,
                "trace_id": trace_id or "",
                "session_id": session_id or "",
                "finish_reason": status,
            }
            event.update(current_usage_attribution())
            event["decision"] = current_call_decision()
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
                    "billed_cost_cny": None,
                    "total_cost": None,
                    "cost_status": "unpriced",
                    "pricing_source": "unpriced",
                    "usage_source": "unavailable",
                })
            ledger_written = bool(get_llm_usage_store().record(event))
        except Exception:
            pass  # 软失败

        finally:
            from backend.infra.llm.budget import record_model_usage

            priced = billing is not None and billing.cost_status in {
                "exact", "estimated",
            }
            try:
                settlement_status = record_model_usage(
                    prompt_tokens=total_tokens,
                    total_tokens=total_tokens,
                    cost=billing.billed_cost_cny if priced else None,
                    review_reason=(
                        "" if status == "success" and total_tokens > 0 and priced
                        else "rerank_usage_or_price_unavailable"
                    ),
                )
            except Exception:
                settlement_status = "accounting_error"
                logger.warning(
                    "[Reranker] 预算结算状态记录失败", exc_info=True,
                )
        return {
            "usage_status": "received" if total_tokens > 0 else "unavailable",
            "settlement_status": settlement_status,
            "usage_ledger_status": "written" if ledger_written else "write_failed",
            "price_version": billing.price_version if billing is not None else "",
            "pricing_source": (
                billing.pricing_source if billing is not None else "unpriced"
            ),
            "cost_status": billing.cost_status if billing is not None else "unpriced",
        }


# ═══════════════════════════════════════════════════════════
# Utility Functions
# ═══════════════════════════════════════════════════════════

def _sigmoid(x: float) -> float:
    """数值稳定的 sigmoid，把 CrossEncoder 输出的 logit 归一化到 0-1。

    BGE-reranker-base 的 predict() 输出范围通常是 [-10, +10](logit)，
    直接拿 logit 与 0.3 比较 → 大多数 chunk 的 score < 0.3，全被过滤。
    必须 sigmoid 归一化后再比较。
    """
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class LocalCrossEncoderBackend(BaseDocumentCompressor):
    """本地 CrossEncoder 实现 (作为 DashScope API 的 fallback)"""

    def __init__(self):
        # 直接调用而非通过实例属性设置以避免 pydantic 约束
        self.__dict__['model'] = LocalModelLoader.get_instance()
        self.__dict__['_backend_name'] = "local"

    def rank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 8,
        *,
        timeout_s: float | None = None,
        deadline_at: float | None = None,
    ) -> list[tuple[int, float]]:
        """
        使用本地 CrossEncoder 进行重排序

        本地推理无 HTTP 超时/计费语义，timeout_s 与 deadline_at 仅为与云端
        后端保持同一调用签名（预算守卫由 RerankCompressor 统一负责）。

        Returns:
            list[tuple[int, float]]: (index, normalized_score) 列表
        """
        if not documents:
            return []
        pairs = [(query, doc) for doc in documents]

        try:
            scores = self.model.predict(pairs)
        except Exception as e:
            logger.error(f"本地 reranker 推理失败: {e}")
            return None

        # 配对并排序
        indexed_scores = list(enumerate(scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)

        # Sigmoid 归一化
        scored = [
            (idx, _sigmoid(float(score)))
            for idx, score in indexed_scores
        ]

        return scored[:top_k]

    def rank_all(
        self,
        query: str,
        documents: list[str],
        *,
        deadline_at: float | None = None,
    ) -> list[tuple[int, float]]:
        """对全部候选评分（本地推理无需裁剪，按批控制单次输入规模）。"""
        scored: list[tuple[int, float]] = []
        total = len(documents)
        for start in range(0, total, _RERANK_BATCH_SIZE):
            batch = documents[start:start + _RERANK_BATCH_SIZE]
            rows = self.rank(query, batch, top_k=len(batch))
            for local_idx, score in rows:
                scored.append((start + local_idx, score))
        return scored

    def rerank_text(self, doc, limit: int = 2000) -> str:
        """本地后端沿用正文截断表达（与既有单查询路径一致）。"""
        return (doc.page_content or "")[:limit]

    def compress_documents(self, documents, query, **kwargs):
        """BaseDocumentCompressor 接口实现（span 埋点在外层 RerankCompressor，见 DashScope 同名说明）"""
        t0 = time.monotonic()

        if not documents:
            return []

        texts = [doc.page_content[:2000] for doc in documents]
        scored_indexed = self.rank(query, texts, top_k=kwargs.get("top_k", RERANK_TOP_K))

        if scored_indexed is None:
            # 超时/失败：透传原文档，标记为不可靠供下游 Gate 决策
            for doc in documents:
                doc.metadata["rerank_unreliable"] = True
            duration_ms = (time.monotonic() - t0) * 1000
            # 记录失败
            self._record_tokens(len(texts), duration_ms, status="error")
            return list(documents)

        # 创建索引映射
        doc_idx_map = {idx: documents[idx] for idx, _ in scored_indexed}

        # 过滤阈值
        threshold = kwargs.get("threshold", RERANK_SCORE_THRESHOLD)
        top_k = kwargs.get("top_k", RERANK_TOP_K)

        result = [
            (doc_idx_map[idx], score)
            for idx, score in scored_indexed
            if score > threshold
        ][:top_k]

        # 写入 rerank_score 到 metadata
        for doc, score in result:
            doc.metadata["rerank_score"] = round(float(score), 4)

        duration_ms = (time.monotonic() - t0) * 1000

        # 记录 token 用量
        self._record_tokens(len(texts), duration_ms)

        return [doc for doc, _ in result]

    def _record_tokens(self, doc_count, duration_ms, status="success"):
        """写入 SQLite（LLMUsageStore）。软失败不影响主流程。"""
        try:
            from backend.observability.llm_usage_store import get_llm_usage_store
            from backend.observability.tracer import current_trace_context
            trace_id, session_id = current_trace_context()
            # Local CrossEncoder 无 token usage API，用估算值
            # 平均每个文档 ~500 chars，按 3 chars/token 估算
            estimated_tokens = doc_count * 150  # 500 / 3 ≈ 167，保守取 150
            get_llm_usage_store().record({
                "component": "rerank",
                "model": RERANKER_MODEL_PATH,
                "provider": "local",
                "prompt_tokens": estimated_tokens,
                "completion_tokens": 0,
                "total_tokens": estimated_tokens,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "trace_id": trace_id or "",
                "session_id": session_id or "",
                "finish_reason": status,
            })
        except Exception:
            pass  # 软失败


# ═══════════════════════════════════════════════════════════
# Factory Pattern - Backend Selector (P0: ENV_MODE Control)
# ═══════════════════════════════════════════════════════════

# Singleton tracker for token tracking
_reranker_tracker = None


def _get_tracker() -> "TokenTracker":
    """懒加载 TokenTracker."""
    global _reranker_tracker
    if _reranker_tracker is None:
        _reranker_tracker = create_tracker_for_rerank(
            log_path=str(Path(TOKEN_USAGE_LOG_PATH).expanduser().resolve()),
            model_name=_configured_rerank_model() if RERANK_PROVIDER == "cloud" else RERANKER_MODEL_PATH,
            backend=RERANK_PROVIDER,
        )
    return _reranker_tracker


def get_reranker_backend() -> BaseDocumentCompressor:
    """
    获取 reranker 后端实例 (工厂函数，P0 架构重构)

    P0 选择逻辑:
      - RERANK_PROVIDER=cloud → DashScopeReranker (强制，API key 错误时抛异常)
      - RERANK_PROVIDER=local → LocalCrossEncoderBackend
      - RERANK_PROVIDER 留空时跟随 ENV_MODE（向后兼容）

    关键约束:
      - 不根据 API key 存在与否自动降级
      - Cloud 模式下 API key 缺失时明确报错
      - Token Tracker 记录用量
    """
    binding = specialized_mod.resolve_binding("rerank")
    if RERANK_PROVIDER == "cloud" or binding is not None:
        # Cloud 模式：强制使用云端 API，缺少 API Key 时明确报错
        # 协议与凭据均由数据库专项绑定解析；不同供应商可以使用不同 Key。
        runtime_config = _resolve_rerank_runtime_config()
        api_key = runtime_config["api_key"]
        if not api_key:
            raise RuntimeError(
                "数据库未配置可用的 rerank 供应商 API Key，请先在管理端测试并保存"
                "重排模型；如需本地模型，请显式使用本地模式。"
            )
        logger.info(f"[Reranker] Cloud 模式初始化完成 (model={runtime_config['model']})")
        return DashScopeReranker(
            api_key=api_key,
            timeout=RERANK_TIMEOUT,
            model=runtime_config["model"],
            api_format=runtime_config["api_format"],
            base_url=runtime_config["base_url"],
            provider_id=runtime_config["provider"],
            billing_model=runtime_config["billing_model"],
        )
    else:
        # Local 模式：使用 CrossEncoder
        logger.info(f"[Reranker] Local 模式初始化完成 (model={RERANKER_MODEL_PATH})")
        return LocalCrossEncoderBackend()



# ═══════════════════════════════════════════════════════════
# 多查询评分：校验 → max 聚合 → 稳定排序（PR #52）
# ═══════════════════════════════════════════════════════════

def _is_real_score(value) -> bool:
    """真实数值分数：排除 bool / str / NaN / Inf。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _is_valid_index(value, candidate_count: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value < candidate_count
    )


def _read_runtime_queries(documents, query: str) -> list[str]:
    """读取 MultiQuery 交接的请求级查询列表；不满足契约时返回空列表。

    契约：首个候选的私有元数据里存放 list[str]，且第一条必须与原问题一致
    （保持"原始 query 恒第一位"）；缺失、类型错误、只有一条或首位不匹配
    一律返回空，调用方回落到既有单查询行为——不启用新的评分语义。
    """
    if not documents:
        return []
    from backend.rag.retrieval.multi_query import RUNTIME_MQ_QUERIES_KEY

    metadata = getattr(documents[0], "metadata", None) or {}
    raw = metadata.get(RUNTIME_MQ_QUERIES_KEY)
    if not isinstance(raw, (list, tuple)):
        return []
    original = (query or "").strip()
    queries: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in queries:
            continue
        queries.append(text)
    if len(queries) < 2 or not original or queries[0] != original:
        return []
    return queries


@contextmanager
def _query_scorer(backend, queries, texts, deadline_at, parent_id):
    """云端三查询共享截止时间并发评分；仅调用方可把分数写回业务结果。"""
    parallel = isinstance(backend, DashScopeReranker) and len(queries) <= 3
    if not parallel or deadline_at is None:
        yield lambda position, query: full_candidate_scores(
            backend, query, texts, deadline_at=deadline_at,
        ), False
        return

    from backend.infra.thread_pools import submit_rag_task
    from backend.observability.tracer import bind_graph_node, reset_graph_node

    primary_ready = threading.Event()
    primary_dispatched = threading.Event()

    def on_primary_dispatch():
        primary_dispatched.set()
        primary_ready.set()

    def invoke(position, query):
        if position > 0:
            primary_ready.wait(timeout=max(0.0, deadline_at - time.monotonic()))
            if not primary_dispatched.is_set():
                return None
        # 排队时间也消耗同一deadline；过期任务不得再触发付费调用。
        if position > 0 and not _budget_allows_extra_query(deadline_at):
            return None
        if time.monotonic() >= deadline_at:
            raise requests.exceptions.Timeout("rerank 排队后共享deadline已过期")
        token = bind_graph_node(parent_id)
        dispatch_token = _primary_rerank_dispatch.set(
            on_primary_dispatch if position == 0 else None,
        )
        index_token = _rerank_query_index.set(position)
        try:
            return full_candidate_scores(backend, query, texts, deadline_at)
        finally:
            if position == 0:
                primary_ready.set()
            _primary_rerank_dispatch.reset(dispatch_token)
            _rerank_query_index.reset(index_token)
            reset_graph_node(token)

    futures = {}
    try:
        for position, query in enumerate(queries):
            if position == 0 or _budget_allows_extra_query(deadline_at):
                futures[position] = submit_rag_task("rerank", invoke, position, query)

        def result(position, query):
            future = futures.get(position)
            if future is None:
                return None
            return future.result(timeout=max(0.0, deadline_at - time.monotonic()))

        yield result, True
    finally:
        # 不等待不能物理取消的Provider；其原调用上下文负责迟到usage结算。
        for future in futures.values():
            future.cancel()


def _supports_full_scoring(backend) -> bool:
    """后端是否具备全候选评分能力（否则退回单查询契约）。"""
    return (
        callable(getattr(backend, "rank_all", None))
        or callable(getattr(backend, "rank", None))
    )


def validate_rerank_scores(
    raw, candidate_count: int,
) -> tuple[list[tuple[int, float]], int, int]:
    """校验 Provider 返回的 (index, score)；返回 (有效项, 非法项数, 重复项数)。

    非法索引、越界索引、重复索引、布尔/字符串/NaN/Inf 分数一律不参与 max；
    缺失评分保持缺失（不默认 0/1），由调用方按查询显式记账。
    """
    if raw is None or isinstance(raw, (str, bytes, dict)):
        raise ValueError(f"rerank 返回类型非法：{type(raw).__name__}")
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"rerank 返回类型非法：{type(raw).__name__}")

    valid: list[tuple[int, float]] = []
    seen: set[int] = set()
    invalid = duplicate = 0
    for row in raw:
        if isinstance(row, (str, bytes, dict)) or not isinstance(row, (list, tuple)):
            invalid += 1
            continue
        if len(row) != 2:
            invalid += 1
            continue
        idx, score = row
        if not _is_valid_index(idx, candidate_count):
            invalid += 1
            continue
        if idx in seen:
            duplicate += 1
            continue
        if not _is_real_score(score):
            invalid += 1
            continue
        value = float(score)
        # 合法区间沿用现有 0-1（DashScope / Jina 协议均如此）；只容忍浮点
        # 表示噪声并夹紧，避免 1.0000000002 之类的噪声把整条查询判废。
        if value < -_RERANK_SCORE_EPS or value > 1.0 + _RERANK_SCORE_EPS:
            invalid += 1
            continue
        value = min(1.0, max(0.0, value))
        seen.add(idx)
        valid.append((idx, value))
    return valid, invalid, duplicate


def aggregate_multi_query_scores(
    queries: list[str],
    per_query_scores: dict[str, list[tuple[int, float]]],
) -> list[tuple[int, float, str]]:
    """同一候选跨查询取最大有效分。

    - 同分不换冠军：先出现的查询获胜，而 queries[0] 恒为原始问题，
      等价于"同分优先原始问题的有效分数"；
    - 排序按分数降序、同分按原候选顺序（稳定），不依赖并发完成顺序；
    - 没有任何有效分数的候选不进入结果（缺失 ≠ 0 分）。
    """
    best: dict[int, tuple[float, int]] = {}
    for query_index, query in enumerate(queries):
        for idx, score in per_query_scores.get(query) or []:
            previous = best.get(idx)
            if previous is None or score > previous[0]:
                best[idx] = (score, query_index)

    ranked = [
        (idx, score, queries[query_index])
        for idx, (score, query_index) in best.items()
    ]
    ranked.sort(key=lambda row: (-row[1], row[0]))
    return ranked


# ═══════════════════════════════════════════════════════════
# Legacy Interface - Backward Compatibility
# ═══════════════════════════════════════════════════════════

class RerankCompressor(BaseDocumentCompressor):
    """将全局重排序包装为 LangChain DocumentCompressor，在 MultiQuery 合并结果后统一执行一次"""

    # Pydantic v2 要求字段必须在类级别声明为 type 注解，且必须有默认值
    top_k: int = RERANK_TOP_K
    threshold: float = RERANK_SCORE_THRESHOLD

    def __init__(self):
        # Pydantic v2 初始化流程：先调用 super().__init__() 设置所有 field，
        # 然后再用 __dict__ 添加非 field 属性（如 backend）
        super().__init__()
        # 此时 self.top_k 应通过 Pydantic 机制可用
        self.__dict__['backend'] = None
        self.__dict__['_backend_type'] = "unknown"
        self.__dict__['_backend_signature'] = None

    def _ensure_backend(self):
        """懒加载后端实例（线程安全）"""
        signature = _reranker_runtime_signature()
        # 兼容测试/调用方注入 backend 的旧契约：没有签名时视为调用方已经
        # 明确提供实例，不因热切换检查把它替换掉。
        if self.backend is not None and self._backend_signature is None:
            self.__dict__['_backend_signature'] = signature
            return
        if self.backend is None or self._backend_signature != signature:
            import threading
            if not hasattr(self, '_ensure_lock'):
                self.__dict__['_ensure_lock'] = threading.Lock()
            with self._ensure_lock:
                if self.backend is None or self._backend_signature != signature:
                    self.__dict__['backend'] = get_reranker_backend()
                    self.__dict__['_backend_signature'] = signature
                    if isinstance(self.backend, DashScopeReranker):
                        self.__dict__['_backend_type'] = "dashscope"
                    else:
                        self.__dict__['_backend_type'] = "local"

    # ── 多查询评分（PR #52）────────────────────────────────────────

    def _log_evictions(self, documents, result_docs, span) -> None:
        """驱逐明细（治理 A）：记录哪些文档被 rerank 淘汰及原因。

        2026-09-13 golden RC-013/080 排查事故：中间阶段静默驱逐文档，
        无任何"谁被淘汰、为什么"的留痕。签名 = (source, 内容前40字)，
        兼容 backend 返回新对象的情况。
        """
        from backend.observability.tracer import trace_collector

        def _sig(d):
            m = d.metadata or {}
            name = m.get("source") or m.get("source_file") or m.get("doc_id") or "?"
            return (name, (d.page_content or "")[:40])

        try:
            survived = {_sig(d) for d in result_docs}
            evicted = [
                {"doc": _sig(d)[0], "content_head": _sig(d)[1][:30]}
                for d in documents if _sig(d) not in survived
            ]
            if evicted:
                trace_collector.add_event(span, "rerank_eviction", "info",
                    f"Rerank 淘汰 {len(evicted)}/{len(documents)} 篇 "
                    f"(低于阈值 {self.threshold} 或超出 top_k={self.top_k})",
                    data={"evicted": evicted[:10], "evicted_count": len(evicted)})
                logger.info(
                    f"[Rerank] 淘汰 {len(evicted)}/{len(documents)} 篇: "
                    f"{[e['doc'] for e in evicted[:5]]}{'...' if len(evicted) > 5 else ''}"
                )
        except Exception:
            pass  # 契约留痕失败不影响主流程

    def _compress_multi_query(self, documents, queries, span, record_stage):
        """对同一批候选按"原问题 + 有效改写"分别全候选评分，取最大值。

        契约：
        - 每条查询都请求全量候选（top_n = 候选数），不按查询各自 Top-K 裁剪；
        - 原始 query 必须评分成功，否则整体降级到既有单查询/透传契约；
        - 额外查询共享检索 deadline，预算不足即跳过，不重开超时窗口；
        - 阈值与 top_k 只在 max 聚合后统一执行一次；
        - 同分优先原始问题，其次原始候选顺序（不依赖并发完成顺序）。
        """
        from backend.observability.tracer import trace_collector

        candidate_count = len(documents)
        texts = [_backend_rerank_text(self.backend, doc) for doc in documents]
        # 并发完成顺序不可用于关联费用；原问题失败出口也保留查询索引映射。
        span.input = {"queries": [q[:80] for q in queries], "candidate_count": candidate_count}
        deadline_at = _budget_deadline_at()
        started = time.monotonic()

        per_query: dict[str, list[tuple[int, float]]] = {}
        query_rows: list[dict] = []
        calls = 0

        with _query_scorer(
            self.backend, queries, texts, deadline_at, span.span_id,
        ) as (score_query, parallel):
            for position, q in enumerate(queries):
                if not parallel and position > 0 and not _budget_allows_extra_query(deadline_at):
                    query_rows.append({
                        "query": q[:80], "status": "skipped_budget",
                        "scored": 0, "invalid": 0, "duplicate": 0, "missing": candidate_count,
                    })
                    trace_collector.add_event(
                        span, "rerank_query_skipped", "info",
                        f"剩余检索预算不足，跳过第 {position + 1} 条查询的评分",
                        data={"query": q[:80]},
                    )
                    logger.info(
                        f"[Rerank] 剩余预算不足，跳过第 {position + 1} 条查询评分"
                    )
                    continue

                try:
                    raw = score_query(position, q)
                    if raw is None:
                        query_rows.append({
                            "query": q[:80], "status": "skipped_budget",
                            "scored": 0, "invalid": 0, "duplicate": 0,
                            "missing": candidate_count,
                        })
                        continue
                    calls += 1
                    valid, invalid, duplicate = validate_rerank_scores(raw, candidate_count)
                except Exception as exc:  # noqa: BLE001 — 单条查询失败不拖垮整链
                    query_rows.append({
                        "query": q[:80], "status": "error",
                        "scored": 0, "invalid": 0, "duplicate": 0,
                        "missing": candidate_count, "error": type(exc).__name__,
                    })
                    trace_collector.add_event(
                        span, "rerank_query_failed", "error",
                        f"查询「{q[:80]}」评分失败：{type(exc).__name__}",
                        data={"query": q[:80], "error": type(exc).__name__},
                    )
                    if position == 0:
                        # 原问题是唯一可靠的基线评分：它失败时不做多查询排序，
                        # 交给既有单查询降级契约（透传 + 不可靠标记）。
                        raise
                    continue

                if not valid:
                    query_rows.append({
                        "query": q[:80], "status": "error",
                        "scored": 0, "invalid": invalid, "duplicate": duplicate,
                        "missing": candidate_count, "error": "no_valid_score",
                    })
                    if position == 0:
                        raise RuntimeError("原问题评分没有有效分数")
                    continue

                per_query[q] = valid
                # 仅白名单标识与数值；不复制正文、私有 runtime 元数据或凭据。
                top_candidates = []
                for idx, score in sorted(valid, key=lambda row: (-row[1], row[0]))[:10]:
                    meta = documents[idx].metadata or {}
                    source = str(meta.get("source") or meta.get("source_file") or "")
                    if "://" in source:
                        from urllib.parse import urlsplit

                        source = urlsplit(source).path
                    source = source.replace("\\", "/").rsplit("/", 1)[-1]
                    top_candidates.append({
                        "index": idx,
                        "chunk_id": str(meta.get("chunk_id") or "")[:160],
                        "doc_id": str(meta.get("doc_id") or "")[:160],
                        "source": source[:160],
                        "score": round(score, 6),
                    })
                query_rows.append({
                    "query": q[:80],
                    "status": (
                        "success" if not invalid and not duplicate
                        and len(valid) == candidate_count else "partial"
                    ),
                    "scored": len(valid), "invalid": invalid, "duplicate": duplicate,
                    "missing": candidate_count - len(valid),
                    "candidate_count": candidate_count,
                    "top_candidates": top_candidates,
                })

        ranked = aggregate_multi_query_scores(queries, per_query)
        for row in query_rows:
            row.setdefault("candidate_count", candidate_count)
        selected = [row for row in ranked if row[1] > self.threshold][: self.top_k]
        for idx, score, winner in selected:
            doc = documents[idx]
            doc.metadata["rerank_score"] = round(float(score), 4)
            doc.metadata["rerank_winner_query"] = winner

        scored_queries = [
            row for row in query_rows if row["status"] in ("success", "partial")
        ]
        skipped = [row for row in query_rows if row["status"] == "skipped_budget"]
        overall = (
            "success" if all(row["status"] == "success" for row in query_rows)
            else "partial" if scored_queries else "degraded"
        )
        for idx, _score, _winner in selected:
            documents[idx].metadata["rerank_status"] = overall

        metrics = {
            "input_docs": candidate_count,
            "output_docs": len(selected),
            "threshold": self.threshold,
            "backend_type": self._backend_type,
            "rerank_mode": "multi_query",
            "scoring_strategy": "bounded_parallel" if parallel else "serial",
            "rerank_status": overall,
            "query_count": len(queries),
            "scored_query_count": len(scored_queries),
            "candidate_count": candidate_count,
            "scored_candidates": len(ranked),
            "missing_candidates": candidate_count - len(ranked),
            "rerank_calls": calls,
            "rerank_ms": round((time.monotonic() - started) * 1000, 1),
            "winning_query": selected[0][2] if selected else "",
            "top_k_scores": [round(float(row[1]), 4) for row in selected],
            "query_status": query_rows,
        }
        if skipped:
            metrics["degrade"] = "budget_insufficient"
        elif overall != "success":
            metrics["degrade"] = "query_scoring_incomplete"

        result_docs = [documents[idx] for idx, _score, _winner in selected]
        trace_collector.add_event(
            span, "multi_query_rerank", "info" if overall == "success" else "warn",
            f"多查询评分 {len(scored_queries)}/{len(queries)} 条查询，"
            f"{candidate_count} 候选 → {len(selected)} 入选",
            data={
                "query_status": query_rows,
                "top_k_scores": metrics["top_k_scores"],
                "winning_query": metrics["winning_query"],
                "degrade": metrics.get("degrade", ""),
            },
        )
        self._log_evictions(documents, result_docs, span)
        trace_collector.end_span(
            span, metrics=metrics,
            status="success" if overall == "success" else "partial",
        )
        record_stage()
        logger.info(
            f"[Rerank] 多查询评分 {len(scored_queries)}/{len(queries)} 条查询 / "
            f"{candidate_count} 候选 → {len(selected)} 入选"
            + (f"（{metrics['degrade']}）" if skipped else "")
        )
        return result_docs

    def compress_documents(self, documents, query, **kwargs):
        from backend.observability.tracer import trace_collector

        # 后端懒加载提前到 span 之前，确保 span name 正确
        try:
            self._ensure_backend()
        except Exception:
            pass  # 下方 try 块会再次尝试并捕获完整异常

        span = trace_collector.start_span("rerank", name=self._backend_type.capitalize())

        def _record_stage() -> None:
            # Phase 6：rerank 阶段长期时序（所有出口统一记账；软失败）
            try:
                from backend.observability.metrics import record_rag_stage

                record_rag_stage("rerank", getattr(span, "duration_ms", 0) or 0)
            except Exception:  # noqa: BLE001 — 观测旁路
                pass

        if not documents:
            trace_collector.end_span(span,
                                     metrics={"input_docs": 0, "output_docs": 0,
                                             "threshold": self.threshold, "backend_type": self._backend_type})
            _record_stage()
            return []

        queries = _read_runtime_queries(documents, query)
        # 单查询保留历史 small_n 行为；多查询不得用默认 1 分冒充有效评分。
        if len(documents) <= 2 and not queries:
            for doc in documents:
                doc.metadata.setdefault("rerank_score", 1.0)
            trace_collector.end_span(span,
                                     metrics={"input_docs": len(documents), "output_docs": len(documents),
                                             "backend_type": self._backend_type, "skipped": "small_n"})
            _record_stage()
            return list(documents)

        in_count = len(documents)

        try:
            self._ensure_backend()

            # Phase C（PR #52）：MultiQuery 交接了有效改写列表时，按多查询
            # 全候选评分；未交接 / 后端不支持全候选评分时保持单查询契约。
            if len(queries) > 1 and _supports_full_scoring(self.backend):
                return self._compress_multi_query(
                    documents, queries, span, _record_stage,
                )

            # 委托给后端实现 - 使用实例的 threshold 属性而非从 config 导入
            result_docs = self.backend.compress_documents(
                list(documents),
                query,
                top_k=self.top_k,
                threshold=self.threshold
            )
            self._log_evictions(documents, result_docs, span)

            trace_collector.end_span(span,
                                 metrics={"input_docs": in_count,
                                         "output_docs": len(result_docs),
                                         "threshold": self.threshold,
                                         "backend_type": self._backend_type,
                                         "rerank_mode": "single_query"})
            _record_stage()
            return result_docs

        except Exception as e:
            trace_collector.end_span(span,
                                   metrics={"input_docs": in_count,
                                           "output_docs": min(in_count, self.top_k),
                                           "threshold": self.threshold,
                                           "backend_type": self._backend_type,
                                           "fallback": "passthrough",
                                           "degrade": "rerank_error",
                                           "error": type(e).__name__},
                                   status="error")
            _record_stage()
            logger.error("RerankCompressor 重排失败（%s），降级透传原文档", type(e).__name__)
            # 降级契约：重排是增强组件，失败不得减少召回数量 —— 透传原文档，
            # 由下游 Evidence Gate 基于其他信号判定，而非静默清空触发误拒答
            return list(documents)[: self.top_k]
