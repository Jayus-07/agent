"""模型治理中心 - 向量维度兼容性校验与 Embedding 变更预检。"""
from __future__ import annotations

import asyncio
from typing import Any
from collections.abc import Mapping
from sqlalchemy import text

from backend.config import model_roles as _real_model_roles
from backend.infra.llm import models as _real_models_mod, registry_store as _real_registry_store
from backend.memory.database import get_session as _real_get_session
from backend.services.model_governance.base import DependencyProxy, ModelConfigConflict, _iso
from backend.services.model_governance.models import _model_validation_issue as _real_model_validation_issue
from backend.shared.logger import logger

get_session = DependencyProxy("get_session", _real_get_session)
model_roles = DependencyProxy("model_roles", _real_model_roles)
models_mod = DependencyProxy("models_mod", _real_models_mod)
registry_store = DependencyProxy("registry_store", _real_registry_store)
_model_validation_issue = DependencyProxy("_model_validation_issue", _real_model_validation_issue)


class CompatibilityMixin:
    """向量库索引与维度兼容性分析能力。"""

    async def preview_embedding_change(self, model_name: str) -> dict:
        """保存前真实检查输出维度，不修改角色、旧索引或凭据。"""
        import asyncio

        if not await registry_store.refresh_registry():
            raise RuntimeError("模型注册表暂不可用，请稍后重试")
        issue = _model_validation_issue(model_name, role="embedding")
        if issue:
            raise ValueError(issue)
        from backend.infra.llm import specialized
        from backend.rag.embedding_identity import embedding_identity_mismatch_reasons
        from backend.rag.embedding_singleton import get_registered_embedding
        from backend.rag.indexing.embedding_migration import (
            storage_snapshot, validate_vectors,
        )

        info = await asyncio.to_thread(storage_snapshot)
        billing_mode = models_mod.get_model_billing(model_name)
        if billing_mode == "metered":
            from backend.infra.llm.pricing import clear_price_cache, get_current_price_table

            try:
                clear_price_cache()
                table = await asyncio.to_thread(
                    get_current_price_table, model_name, "embedding",
                )
                table.require(model_name, "embedding", enforce=True)
            except Exception as exc:
                raise ValueError("云端嵌入模型缺少有效或一致的价格，请先配置价格") from exc
        embedding = await asyncio.to_thread(get_registered_embedding, model_name)
        binding = getattr(embedding, "_fixed_binding", None) or specialized.registered_binding(
            "embedding", model_name,
        )
        fingerprint = specialized.embedding_fingerprint(binding)
        from backend.core.request_context import get_tool_tenant_id, get_tool_user_id
        from backend.infra.llm.budget import bind_request_budget
        from backend.observability.tracer import trace_collector
        import time

        trace = trace_collector.start("嵌入模型切换预检", session_id="embedding-preview")
        started = time.monotonic()
        bind_request_budget(trace.id, user_id=get_tool_user_id(),
                            tenant_id=get_tool_tenant_id())
        try:
            vectors = await asyncio.to_thread(
                embedding.embed_documents, ["向量模型切换兼容性检查"],
            )
            validate_vectors(vectors, 1, info["dimension"])
        except Exception:
            trace.status = "error"
            trace_collector.finish(trace, "预检失败，未改变模型和索引",
                                   int((time.monotonic()-started)*1000), model_name)
            raise
        finally:
            close = getattr(embedding, "close", None)
            if callable(close):
                close()
        trace_collector.finish(trace, "预检通过",
                               int((time.monotonic()-started)*1000), model_name)
        current = model_roles.resolve_effective("embedding")["value"]
        target_identity = {
            "provider": binding.provider_id,
            "model": binding.model_name,
            "revision": fingerprint,
        }
        incompatible_count = sum(
            bool(
                item.get("status") != "ready"
                or embedding_identity_mismatch_reasons(
                    {
                        "provider": item.get("provider"),
                        "model": item.get("model"),
                        "revision": item.get("fingerprint"),
                    },
                    target_identity,
                    has_vectors=int(item.get("vectors") or 0) > 0,
                )
                or (
                    int(item.get("vectors") or 0) > 0
                    and int(item.get("dimension") or 0) != info["dimension"]
                )
            )
            for item in info["collections"]
        )
        requires_rebuild = bool(incompatible_count) or (
            info["vectors"] > 0 and current != model_name
        )
        return {
            **info, "modelName": model_name,
            "currentModel": current, "fingerprint": fingerprint,
            "billing": billing_mode,
            "provider": binding.provider_id,
            "requiresRebuild": requires_rebuild, "traceId": trace.id,
            "modelChanged": current != model_name,
            "incompatibleCollections": incompatible_count,
        }


    async def get_index_compat(self) -> list[dict]:
        """全部向量索引的 embedding 元数据（与运行时角色对比供前端展示）。"""
        import asyncio

        from backend.infra.llm import specialized
        from backend.rag.embedding_identity import embedding_identity_mismatch_reasons
        from backend.rag.indexing.embedding_migration import (
            EMBEDDING_DIM, read_index_metadata,
        )

        rows = await asyncio.to_thread(read_index_metadata)
        runtime_model = str(
            model_roles.resolve_effective("embedding").get("value") or ""
        )
        runtime_provider = ""
        runtime_fingerprint = ""
        try:
            binding = specialized.registered_binding("embedding", runtime_model)
            runtime_provider = binding.provider_id
            runtime_fingerprint = specialized.embedding_fingerprint(binding)
        except Exception:
            # 无法从注册表确立完整身份时，非空索引会按不兼容展示并由检索门禁拒绝。
            pass
        out: list[dict] = []
        for r in rows:
            if r["collection"] == "__embedding_control__" or "::cand:" in r["collection"]:
                continue
            index_model = str(r["embedding_model"] or "")
            index_provider = str(r["embedding_provider"] or "")
            index_fingerprint = str(r["model_revision"] or "")
            has_vectors = bool(r.get("has_vectors"))
            raw_status = r.get("status")
            status = str(raw_status or ("rebuild_required" if has_vectors else "ready"))
            mismatch_reasons = embedding_identity_mismatch_reasons(
                {
                    "provider": index_provider,
                    "model": index_model,
                    "revision": index_fingerprint,
                },
                {
                    "provider": runtime_provider,
                    "model": runtime_model,
                    "revision": runtime_fingerprint,
                },
                has_vectors=has_vectors,
            )
            if has_vectors and raw_status is None:
                mismatch_reasons.append("index_metadata_missing")
            if (has_vectors
                    and int(r["embedding_dimension"] or 0) != EMBEDDING_DIM):
                mismatch_reasons.append("dimension_mismatch")
            if status != "ready":
                mismatch_reasons.append("index_not_ready")
            mismatch = bool(mismatch_reasons)
            if mismatch and status == "ready":
                status = "rebuild_required"
            out.append({
                "collection": str(r["collection"]),
                "embeddingProvider": index_provider,
                "embeddingModel": index_model,
                "modelRevision": index_fingerprint,
                "embeddingDimension": int(r["embedding_dimension"] or 0),
                "indexVersion": int(r["index_version"] or 1),
                "builtAt": _iso(r["built_at"]),
                "updatedAt": _iso(r["updated_at"]),
                "status": status,
                "runtimeProvider": runtime_provider,
                "runtimeModel": runtime_model,
                "mismatchReasons": sorted(set(mismatch_reasons)),
                "mismatch": mismatch,
            })
        return out

