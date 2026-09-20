"""Token Tracker - Token 用量追踪器 (P0 - 硬编码约束实现)

架构设计原则:
1. TokenUsageEvent 是统一事件模型，JSONL/Prometheus/Trace/Evaluation 都是消费者
2. Token Tracker 与 Trace 存储解耦，只生成事件不直接操作 Trace
3. Prometheus 使用独立 metric token_usage_total，保持 llm_tokens_total 向后兼容
4. Local 模式禁止伪造 Token，无法获取 usage 时 total_tokens=null

TokenUsageEvent Schema:
{
    "component": "embedding"|"rerank"|"llm",
    "model_name": str,
    "backend": "cloud"|"local",
    "prompt_tokens": int|null,
    "completion_tokens": int|null,
    "total_tokens": int|null,
    "token_usage_available": bool,
    "duration_ms": float,
    "status": "success"|"error",
    "error": str|null,
    "timestamp": ISO8601,
    "trace_id": str|null,        # Optional
    "evaluation_run_id": str|null  # Optional
}
"""
from __future__ import annotations

import functools
import json
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional, Any


@dataclass
class TokenUsageEvent:
    """统一的 Token Usage Event 模型。"""
    component: str  # embedding | rerank | llm
    model_name: str
    backend: str  # cloud | local
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    token_usage_available: bool
    duration_ms: float
    status: str  # success | error
    error: Optional[str] = None
    timestamp: str = ""
    trace_id: Optional[str] = None
    evaluation_run_id: Optional[str] = None
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    request_id: Optional[str] = None
    decision: str = "primary"
    run_id: Optional[str] = None
    step_id: Optional[str] = None
    role: Optional[str] = None
    stage: Optional[str] = None
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = self._now_iso()
    
    @staticmethod
    def _now_iso() -> str:
        """ISO8601 UTC timestamp."""
        t = time.time()
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class TokenTracker:
    """Token 用量追踪器 — JSONL + Prometheus (decoupled from Trace).
    
    关键设计:
    - 不直接与 TraceCollector 耦合，只生成 TokenUsageEvent
    - Prometheus 使用独立 metric token_usage_total
    - LLM Token 统计由 proxy.py 负责，本 tracker 仅用于 Embedding/Rerank
    """
    
    def __init__(
        self,
        component: str,
        log_path: str,
        model_name: str = "",
        backend: str = "cloud",
        trace_id: Optional[str] = None,
        evaluation_run_id: Optional[str] = None,
    ):
        """
        Args:
            component: "embedding" | "rerank"
            log_path: JSONL 文件路径 (如 data/token_usage.jsonl)
            model_name: 模型名称
            backend: "cloud" | "local"
            trace_id: 关联的 Trace ID (Optional)
            evaluation_run_id: 关联的 Evaluation Run ID (Optional)
        """
        if component not in ("embedding", "rerank"):
            raise ValueError(f"component 必须是 'embedding' 或 'rerank', 当前为：'{component}'")
        
        self.component = component
        self.model_name = model_name
        self.backend = backend
        
        # 自动创建父目录
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._lock = threading.Lock()
        self._trace_id = trace_id
        self._evaluation_run_id = evaluation_run_id
    
    def track(self, func: Callable) -> Callable:
        """装饰器：记录 API 调用的 Token 用量到 JSONL + Prometheus."""
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            from backend.infra.llm.budget import reserve_model_call

            # embedding/rerank 也属于本次请求的模型调用，和 proxy 的
            # chat 调用共用同一请求级预算。
            reserve_model_call(
                "primary", model_name=self.model_name, component=self.component,
            )
            t0 = time.monotonic()
            status = "success"
            error = None
            total_tokens = None
            prompt_tokens = None
            completion_tokens = None
            
            try:
                result = func(*args, **kwargs)
                
                # 尝试从返回值提取 token 用量
                extracted = self._extract_usage(result)
                if extracted is not None:
                    prompt_tokens = extracted.get("prompt_tokens")
                    completion_tokens = extracted.get("completion_tokens")
                    total_tokens = extracted.get("total_tokens")
                    token_usage_available = True
                else:
                    # Local 模式无法获取 usage → null
                    token_usage_available = False
                
                status = "success"
                return result
                
            except Exception as e:
                status = "error"
                error = str(e)[:200]
                token_usage_available = False
                raise
                
            finally:
                duration_ms = (time.monotonic() - t0) * 1000
                
                # 构建 Event
                event = TokenUsageEvent(
                    component=self.component,
                    model_name=self.model_name,
                    backend=self.backend,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    token_usage_available=token_usage_available,
                    duration_ms=round(duration_ms, 2),
                    status=status,
                    error=error,
                    trace_id=self._trace_id,
                    evaluation_run_id=self._evaluation_run_id,
                )
                try:
                    from backend.observability.llm_usage_store import (
                        current_usage_attribution,
                    )
                    attribution = current_usage_attribution()
                    event.trace_id = event.trace_id or attribution["trace_id"]
                    event.user_id = attribution["user_id"]
                    event.tenant_id = attribution["tenant_id"]
                    event.request_id = attribution["request_id"]
                    event.run_id = attribution["run_id"] or None
                    event.step_id = attribution["step_id"] or None
                    event.role = attribution["role"] or None
                    event.stage = attribution["stage"] or None
                except Exception:
                    pass
                try:
                    from backend.infra.llm.budget import current_call_decision

                    event.decision = current_call_decision()
                except Exception:
                    pass

                try:
                    from backend.infra.llm.budget import record_model_usage
                    from backend.infra.llm.budget import current_request_budget
                    from backend.infra.llm.pricing import calculate_current_cost

                    budget_state = current_request_budget()
                    cost_usd = calculate_current_cost(
                        self.model_name,
                        self.component,
                        {"input": total_tokens or 0},
                        enforce=bool(
                            budget_state
                            and budget_state.mode == "enforce"
                            and budget_state.quota_store is not None
                        ),
                    )

                    record_model_usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        cost_usd=cost_usd,
                    )
                except Exception:
                    # 预算统计失败不得覆盖原始模型结果/异常。
                    pass
                
                # JSONL 写入 (线程安全)
                self._write_jsonl(event)
                
                # SQLite 写入（统一数据源供看板聚合）
                self._write_sqlite(event)
                
                # Prometheus 指标 (独立 metric)
                self._record_prometheus(event)
                
                # 结构化日志
                logger_info = (
                    f"[TokenTracker] {self.component} "
                    f"tokens={total_tokens} usage_avail={token_usage_available} "
                    f"duration={duration_ms:.2f}ms status={status}"
                )
                if error:
                    logger_info += f" error={error}"
                
                # Safe logging: lazy import to avoid None issue
                try:
                    from backend.shared.logger import logger as _logger
                    _logger.debug(logger_info)
                except Exception:
                    pass  # Logging failure should not break main flow
        
        return wrapper
    
    def _extract_usage(self, result) -> Optional[dict]:
        """从返回值提取 token 用量。Local 模式通常无 usage，返回 None。"""
        # 具体实现由调用方提供 extract_token_usage 方法
        # 默认返回 None (Local 模式)
        return None
    
    def _write_jsonl(self, event: TokenUsageEvent):
        """线程安全写入 JSONL。"""
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(event.to_json() + "\n")
    
    def _write_sqlite(self, event: TokenUsageEvent):
        """同步写入 SQLite（LLMUsageStore），供看板聚合。软失败不阻塞主流程。"""
        try:
            from backend.infra.llm.models import compute_embedding_cost
            from backend.observability.llm_usage_store import get_llm_usage_store

            tokens = event.total_tokens or 0
            cost_usd = compute_embedding_cost(event.model_name, tokens) if event.backend == "cloud" else 0.0

            store = get_llm_usage_store()
            store.record({
                "component": event.component,
                "model": event.model_name,
                "provider": "dashscope" if event.backend == "cloud" else "local",
                "prompt_tokens": tokens,
                "completion_tokens": 0,
                "total_tokens": tokens,
                "cost_usd": cost_usd,
                "duration_ms": event.duration_ms,
                "trace_id": event.trace_id or "",
                "request_id": event.request_id or "",
                "session_id": "",
                "user_id": event.user_id or "",
                "tenant_id": event.tenant_id or "",
                "decision": event.decision,
                "finish_reason": event.status,
                "run_id": event.run_id or "",
                "step_id": event.step_id or "",
                "role": event.role or "",
                "stage": event.stage or "",
            })
        except Exception as e:
            # 软失败：SQLite 写入失败不影响主流程和 JSONL 记录
            try:
                from backend.shared.logger import logger as _logger
                _logger.debug(f"[TokenTracker] SQLite 写入失败: {e}")
            except Exception:
                pass
    
    def _record_prometheus(self, event: TokenUsageEvent):
        """记录到 Prometheus - 使用独立 metric token_usage_total。"""
        try:
            from backend.observability.metrics import token_usage_total
            
            if event.total_tokens is not None and event.total_tokens > 0:
                token_usage_total.labels(
                    component=event.component,
                    model=event.model_name,
                    direction="total",
                ).inc(event.total_tokens)
            
            # prompt/completion 分开统计
            if event.prompt_tokens is not None and event.prompt_tokens > 0:
                token_usage_total.labels(
                    component=event.component,
                    model=event.model_name,
                    direction="prompt",
                ).inc(event.prompt_tokens)
            
            if event.completion_tokens is not None and event.completion_tokens > 0:
                token_usage_total.labels(
                    component=event.component,
                    model=event.model_name,
                    direction="completion",
                ).inc(event.completion_tokens)
                
        except Exception:
            # 软失败：指标记录失败不影响主流程
            pass


# =====================================================
# Module-level helpers
# =====================================================

def create_tracker_for_embedding(
    log_path: str,
    model_name: str,
    backend: str,
    trace_id: Optional[str] = None,
    evaluation_run_id: Optional[str] = None,
) -> TokenTracker:
    """Factory: 创建 Embedding TokenTracker."""
    return TokenTracker(
        component="embedding",
        log_path=log_path,
        model_name=model_name,
        backend=backend,
        trace_id=trace_id,
        evaluation_run_id=evaluation_run_id,
    )


def create_tracker_for_rerank(
    log_path: str,
    model_name: str,
    backend: str,
    trace_id: Optional[str] = None,
    evaluation_run_id: Optional[str] = None,
) -> TokenTracker:
    """Factory: 创建 Rerank TokenTracker."""
    return TokenTracker(
        component="rerank",
        log_path=log_path,
        model_name=model_name,
        backend=backend,
        trace_id=trace_id,
        evaluation_run_id=evaluation_run_id,
    )


logger = None  # Lazy import in wrapper


def _get_logger():
    global logger
    if logger is None:
        from backend.shared.logger import logger as _logger
        logger = _logger
    return logger
