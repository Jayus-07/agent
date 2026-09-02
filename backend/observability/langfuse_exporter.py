"""Langfuse 上报/读取层 — trace 主存储迁移（替代 SQLite 作为主查询源）。

写入：POST {host}/api/public/ingestion（批量 trace-create + observation-create），
     startTime/endTime 显式携带，保证 Langfuse 时间线与自研 span 计时完全一致。
读取：GET /api/public/traces[/{id}] + /api/public/observations，
     重建为与 SQLite dict 兼容的结构，路由层无需感知数据源切换。

原则（与 alerts/trace_store 一致）：
  - 所有对外方法软失败：Langfuse 不可达时只记日志，绝不阻塞业务链路
  - SQLite TraceStore 仍由 tracer 保留写入，作为降级兜底
"""
from __future__ import annotations

import base64
import os
import time
import uuid
from typing import Any

from backend.shared.logger import logger


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


class LangfuseExporter:
    """Langfuse REST 客户端（requests 直连，不依赖 SDK 的 OTel 上下文）。"""

    def __init__(self, host: str = "", public_key: str = "", secret_key: str = "",
                 timeout: float = 5.0):
        self.host = (host or _env("LANGFUSE_HOST", "http://localhost:3001")).rstrip("/")
        self.public_key = public_key or _env("LANGFUSE_PUBLIC_KEY")
        self.secret_key = secret_key or _env("LANGFUSE_SECRET_KEY")
        self.timeout = timeout
        self._warned_down = False  # 连续不可达只警告一次，避免日志风暴

    # ── 状态 ──

    @property
    def enabled(self) -> bool:
        """开关：显式 false 关闭；否则要求 host + 双 key 齐全。"""
        if _env("LANGFUSE_ENABLED", "true").lower() in ("0", "false", "no"):
            return False
        return bool(self.host and self.public_key and self.secret_key)

    def _headers(self) -> dict:
        token = base64.b64encode(
            f"{self.public_key}:{self.secret_key}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Any | None:
        """统一 HTTP 出口：任何异常返回 None（软失败）。"""
        if not self.enabled:
            return None
        import requests
        url = f"{self.host}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
            if resp.status_code >= 300:
                self._warn(f"{method} {path} → {resp.status_code}: {resp.text[:200]}")
                return None
            self._warned_down = False
            if resp.content:
                return resp.json()
            return {}
        except Exception as e:
            self._warn(f"{method} {path} 失败: {e}")
            return None

    def _warn(self, msg: str):
        if not self._warned_down:
            logger.warning(f"[Langfuse] {msg}")
            self._warned_down = True
        else:
            logger.debug(f"[Langfuse] {msg}")

    # ═══════════════════════════════════════════
    # 写入：TraceRecord → ingestion batch
    # ═══════════════════════════════════════════

    def export_trace(self, record) -> bool:
        """把完整 TraceRecord 推送到 Langfuse。成功返回 True。"""
        if not self.enabled:
            return False
        try:
            batch = self._build_batch(record)
        except Exception:
            logger.warning("[Langfuse] 构造 ingestion batch 失败", exc_info=True)
            return False
        result = self._request("POST", "/api/public/ingestion", json={"batch": batch})
        if result is None:
            return False
        errors = [e for e in result.get("errors", [])] if isinstance(result, dict) else []
        if errors:
            logger.warning(f"[Langfuse] ingestion 部分失败: {errors[:3]}")
            return False
        return True

    def export_trace_dict(self, data: dict) -> bool:
        """把已序列化的 trace dict 推送到 Langfuse（供 trace_writer 异步路径）。"""
        if not self.enabled:
            return False
        try:
            batch = self._build_batch_from_dict(data)
        except Exception:
            logger.warning("[Langfuse] 构造 ingestion batch(dict) 失败", exc_info=True)
            return False
        result = self._request("POST", "/api/public/ingestion", json={"batch": batch})
        if result is None:
            return False
        errors = [e for e in result.get("errors", [])] if isinstance(result, dict) else []
        if errors:
            logger.warning(f"[Langfuse] ingestion(dict) 部分失败: {errors[:3]}")
            return False
        return True

    def _build_batch_from_dict(self, data: dict) -> list[dict]:
        """dict → [trace-create, observation-create * N]。"""
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        spans = data.get("spans") or []

        rec_meta = {
            "question": data.get("question", ""),
            "answer_preview": data.get("answer_preview", ""),
            "answer_len": data.get("answer_len", 0),
            "duration_ms": data.get("total_ms") or data.get("duration_ms", 0),
            "model": data.get("model", ""),
            "provider": data.get("provider", ""),
            "usage": data.get("usage", {}),
            "cost": data.get("cost", {}),
            "error": data.get("error", {}),
            "status": data.get("status", "success"),
            "workflow_name": data.get("workflow_name", ""),
            "workflow_kind": data.get("workflow_kind", "other"),
            "root_span_id": data.get("root_span_id", ""),
            "parent_id": data.get("parent_id"),
            "children_ids": data.get("children_ids", []),
            "sla_threshold_ms": data.get("sla_threshold_ms", 10000),
            "tags": data.get("tags", {}),
            "request_id": data.get("request_id", ""),
            "record_metadata": data.get("metadata", {}),
        }

        batch = [{
            "id": uuid.uuid4().hex,
            "type": "trace-create",
            "timestamp": now_iso,
            "body": {
                "id": data.get("id", ""),
                "timestamp": data.get("timestamp") or now_iso,
                "name": data.get("workflow_name") or "rag_agent",
                "input": data.get("question", ""),
                "output": data.get("answer_preview", ""),
                "sessionId": data.get("session_id") or None,
                "metadata": rec_meta,
                "tags": [t for t in [data.get("workflow_kind"),
                                     data.get("status", "success")] if t],
                "release": "agent-platform",
            },
        }]

        for s in spans:
            if isinstance(s, dict):
                batch.append(self._build_observation_from_dict(
                    data.get("id", ""), s, now_iso))
        return batch

    def _build_observation_from_dict(self, trace_id: str, span: dict,
                                     now_iso: str) -> dict:
        """Span dict → observation-create。"""
        metrics = span.get("metrics") or {}
        span_type = span.get("type", "")
        span_kind = span.get("kind", "")
        is_llm = span_type == "llm_call" or span_kind == "llm"
        body: dict = {
            "id": self._obs_id(trace_id, span.get("span_id", "")),
            "traceId": trace_id,
            "type": "GENERATION" if is_llm else "SPAN",
            "name": span.get("name") or span.get("span_id", ""),
            "startTime": span.get("start_time") or now_iso,
            "endTime": span.get("end_time") or now_iso,
            "input": span.get("input"),
            "output": span.get("output"),
            "level": "ERROR" if span.get("status") == "error" else "DEFAULT",
            "metadata": {
                "span_id": span.get("span_id", ""),
                "type": span_type,
                "kind": span_kind,
                "status": span.get("status", "success"),
                "sequence": span.get("sequence", 0),
                "retry_count": span.get("retry_count", 0),
                "metrics": metrics,
                "events": span.get("events", []),
                "errors": span.get("errors", []),
            },
        }
        parent_id = span.get("parent_id")
        if parent_id:
            body["parentObservationId"] = self._obs_id(trace_id, parent_id)
        if is_llm:
            body["model"] = metrics.get("model_name", "")
            if metrics.get("total_tokens"):
                body["usageDetails"] = {
                    "input": metrics.get("prompt_tokens", 0),
                    "output": metrics.get("completion_tokens", 0),
                    "total": metrics.get("total_tokens", 0),
                }
            if metrics.get("cost_usd"):
                body["costDetails"] = {
                    "cost_amount": metrics["cost_usd"],
                    "cost_currency": "USD",
                }
        return {
            "id": uuid.uuid4().hex,
            "type": "observation-create",
            "timestamp": now_iso,
            "body": body,
        }

    def _build_batch(self, record) -> list[dict]:
        """TraceRecord → [trace-create, observation-create * N]。"""
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        spans = getattr(record, "spans", []) or []

        # 对等字段全部放进 metadata，读取时可无损重建 DTO
        rec_meta = {
            "question": record.question,
            "answer_preview": record.answer_preview,
            "answer_len": record.answer_len,
            "duration_ms": record.total_ms or record.duration_ms,
            "model": record.model,
            "provider": record.provider,
            "usage": record.usage,
            "cost": record.cost,
            "error": record.error,
            "status": getattr(record, "status", "success"),
            "workflow_name": record.workflow_name,
            "workflow_kind": record.workflow_kind,
            "root_span_id": record.root_span_id,
            "parent_id": record.parent_id,
            "children_ids": record.children_ids,
            "sla_threshold_ms": record.sla_threshold_ms,
            "tags": record.tags,
            "request_id": record.request_id,
            # 业务侧 metadata（Evidence Gate rejection 等）嵌套存放
            "record_metadata": record.metadata,
        }

        batch = [{
            "id": uuid.uuid4().hex,
            "type": "trace-create",
            "timestamp": now_iso,
            "body": {
                "id": record.id,
                "timestamp": record.timestamp or now_iso,
                "name": record.workflow_name or "rag_agent",
                "input": record.question,
                "output": record.answer_preview,
                "sessionId": record.session_id or None,
                "metadata": rec_meta,
                "tags": [t for t in [record.workflow_kind,
                                     getattr(record, "status", "success")] if t],
                "release": "agent-platform",
            },
        }]

        for s in spans:
            batch.append(self._build_observation(record.id, s, now_iso))
        return batch

    def _build_observation(self, trace_id: str, span, now_iso: str) -> dict:
        """Span → observation-create（llm_call → GENERATION，其余 → SPAN）。"""
        metrics = span.metrics or {}
        is_llm = span.type == "llm_call" or span.kind == "llm"
        body: dict = {
            "id": self._obs_id(trace_id, span.span_id),
            "traceId": trace_id,
            "type": "GENERATION" if is_llm else "SPAN",
            "name": span.name or span.span_id,
            "startTime": span.start_time or now_iso,
            "endTime": span.end_time or now_iso,
            "input": span.input,
            "output": span.output,
            "level": "ERROR" if span.status == "error" else "DEFAULT",
            "metadata": {
                "span_id": span.span_id,
                "type": span.type,
                "kind": span.kind,
                "status": span.status,
                "sequence": span.sequence,
                "retry_count": span.retry_count,
                "metrics": metrics,
                "events": span.events,
                "errors": span.errors,
            },
        }
        if span.parent_id:
            body["parentObservationId"] = self._obs_id(trace_id, span.parent_id)
        if is_llm:
            body["model"] = metrics.get("model_name", "")
            if metrics.get("total_tokens"):
                body["usageDetails"] = {
                    "input": metrics.get("prompt_tokens", 0),
                    "output": metrics.get("completion_tokens", 0),
                    "total": metrics.get("total_tokens", 0),
                }
            if metrics.get("cost_usd"):
                body["costDetails"] = {
                    "cost_amount": metrics["cost_usd"],
                    "cost_currency": "USD",
                }
        return {
            "id": uuid.uuid4().hex,
            "type": "observation-create",
            "timestamp": now_iso,
            "body": body,
        }

    @staticmethod
    def _obs_id(trace_id: str, span_id: str) -> str:
        return f"{trace_id}:{span_id}"

    # ═══════════════════════════════════════════
    # 读取：public API → SQLite 兼容 dict
    # ═══════════════════════════════════════════

    def list_traces(self, limit: int = 20) -> list[dict]:
        """最近 N 条 trace 摘要（不含 spans），结构与 TraceStore.list 对齐。

        Langfuse API limit 上限 100，超出时分页拉取。
        """
        result: list[dict] = []
        remaining = limit
        page = 1
        while remaining > 0 and page <= 5:
            batch_size = min(remaining, 100)
            data = self._request(
                "GET", f"/api/public/traces?limit={batch_size}&page={page}")
            if not data:
                break
            for t in (data.get("data") or []):
                d = self._trace_to_summary(t)
                if d:
                    result.append(d)
            meta = data.get("meta") or {}
            if page >= (meta.get("totalPages") or 1):
                break
            remaining -= batch_size
            page += 1
        return result[:limit]

    def get_trace(self, trace_id: str) -> dict | None:
        """单条 trace 完整详情（含 spans 重建）。"""
        t = self._request("GET", f"/api/public/traces/{trace_id}")
        if not t:
            return None
        d = self._trace_to_summary(t)
        if d is None:
            return None
        d["spans"] = [
            self._observation_to_span(o) for o in self._list_observations(trace_id)
        ]
        d["spans"].sort(key=lambda s: s.get("sequence", 0))
        return d

    def _list_observations(self, trace_id: str) -> list[dict]:
        """分页拉取 trace 的全部 observations（API limit 上限 100）。"""
        result: list[dict] = []
        for page in range(1, 6):  # 最多 5 页 = 500 个 span，超出概率极低
            data = self._request(
                "GET", f"/api/public/observations?traceId={trace_id}&limit=100&page={page}")
            if not data:
                break
            items = data.get("data") or []
            result.extend(items)
            meta = data.get("meta") or {}
            if page >= (meta.get("totalPages") or 1):
                break
        return result

    def _trace_to_summary(self, t: dict) -> dict | None:
        """Langfuse trace → 摘要 dict（优先用上报时存的 rec_meta 无损重建）。"""
        meta = t.get("metadata") or {}
        rec = meta if isinstance(meta.get("workflow_name"), str) else {}
        duration = rec.get("duration_ms", 0) or 0
        return {
            "id": t.get("id", ""),
            "timestamp": (t.get("timestamp") or "")[:19].replace("T", "T"),
            "session_id": t.get("sessionId") or "",
            "question": rec.get("question", t.get("input") or ""),
            "answer_preview": rec.get("answer_preview", t.get("output") or ""),
            "answer_len": rec.get("answer_len", 0),
            "duration_ms": duration,
            "model": rec.get("model", ""),
            "provider": rec.get("provider", ""),
            "usage": rec.get("usage", {}),
            "cost": rec.get("cost", {}),
            "cost_usd": (rec.get("cost") or {}).get("total_usd", 0) if isinstance(rec.get("cost"), dict) else 0,
            "error": rec.get("error", {}),
            "metadata": rec.get("record_metadata", {}),
            "status": rec.get("status", "success"),
            "workflow_name": rec.get("workflow_name", t.get("name") or ""),
            "workflow_kind": rec.get("workflow_kind", "other"),
            "root_span_id": rec.get("root_span_id", ""),
            "parent_id": rec.get("parent_id"),
            "children_ids": rec.get("children_ids", []),
            "sla_threshold_ms": rec.get("sla_threshold_ms", 10000),
            "tags": rec.get("tags", {}),
        }

    def _observation_to_span(self, o: dict) -> dict:
        """Langfuse observation → Span 兼容 dict。"""
        meta = o.get("metadata") or {}
        start = o.get("startTime") or ""
        end = o.get("endTime") or ""
        duration = meta.get("metrics", {}).get("elapsed_ms") if isinstance(meta.get("metrics"), dict) else None
        if not duration and start and end:
            duration = self._diff_ms(start, end)
        status = meta.get("status", "error" if o.get("level") == "ERROR" else "success")
        return {
            "span_id": meta.get("span_id", self._strip_obs_prefix(o.get("id", ""))),
            "parent_id": self._strip_obs_prefix(o.get("parentObservationId") or "") or None,
            "name": o.get("name", ""),
            "type": meta.get("type", "llm_call" if o.get("type") == "GENERATION" else "tool_call"),
            "kind": meta.get("kind", "tool"),
            "status": status,
            "start_time": start,
            "end_time": end,
            "duration_ms": int(duration or 0),
            "sequence": meta.get("sequence", 0),
            "retry_count": meta.get("retry_count", 0),
            "metrics": meta.get("metrics", {}),
            "input": o.get("input"),
            "output": o.get("output"),
            "events": meta.get("events", []),
            "errors": meta.get("errors", []),
        }

    @staticmethod
    def _strip_obs_prefix(obs_id: str) -> str:
        """'traceid:spanid' → 'spanid'。"""
        return obs_id.split(":", 1)[1] if ":" in obs_id else obs_id

    @staticmethod
    def _diff_ms(start_iso: str, end_iso: str) -> int:
        """ISO 时间差（毫秒）。解析失败返回 0。"""
        from datetime import datetime
        def _parse(s: str):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        try:
            return int((_parse(end_iso) - _parse(start_iso)).total_seconds() * 1000)
        except Exception:
            return 0


# ── 模块级单例 ──

_exporter: LangfuseExporter | None = None


def get_langfuse_exporter() -> LangfuseExporter:
    global _exporter
    if _exporter is None:
        _exporter = LangfuseExporter()
    return _exporter
