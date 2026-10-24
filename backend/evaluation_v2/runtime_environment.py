"""生成并核对 V2 RAG Run 的运行配置指纹。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict


class RuntimeEnvironmentMismatchError(RuntimeError):
    """Run 创建后检索配置发生变化，不能继续生成可比较结果。"""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        if reason_code == "runtime_snapshot_missing":
            message = "运行快照缺少检索环境指纹"
        else:
            message = "运行期间检索环境配置已变化"
        super().__init__(message)


_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")


def _safe_identifier(value: str) -> str:
    """保留常见模型标识；任意文本和疑似凭据只保留不可逆指纹。"""
    normalized = str(value or "").strip()
    lowered = normalized.lower()
    if (
        _SAFE_IDENTIFIER_RE.fullmatch(normalized)
        and not lowered.startswith(("sk-", "bearer-", "token-"))
        and "api_key" not in lowered
    ):
        return normalized
    if not normalized:
        return ""
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]}"


def _local_model_identity(value: str) -> dict[str, str]:
    """只保存本地模型的名称和路径指纹，不暴露机器绝对路径。"""
    normalized = str(value or "").replace("\\", "/").rstrip("/")
    name = _safe_identifier(normalized.rsplit("/", 1)[-1])
    return {
        "model": name,
        "path_fingerprint": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        if normalized
        else "",
    }


def _model_role_snapshot(
    *, role: str, provider_mode: str, configured_model: str, local_model_path: str,
) -> dict:
    from backend.config import model_roles

    policy = asdict(model_roles.resolve_runtime_policy(role))
    policy["fallback_model"] = _safe_identifier(policy.get("fallback_model", ""))
    if provider_mode == "local":
        return {
            "provider_mode": provider_mode,
            **_local_model_identity(local_model_path),
            "policy": policy,
        }

    model = model_roles.resolve_runtime_name(role, configured_model)
    return {
        "provider_mode": provider_mode,
        "model": _safe_identifier(model),
        "provider": _safe_identifier(model_roles.provider_of(model) or ""),
        "policy": policy,
    }


def current_runtime_environment() -> dict:
    """返回不包含凭据、URL 或绝对本地路径的 RAG 检索环境快照。"""
    import backend.config.llm as llm_config
    import backend.config.rag as rag_config

    models = {
        "embedding": _model_role_snapshot(
            role="embedding",
            provider_mode=llm_config.EMBEDDING_PROVIDER,
            configured_model=llm_config.EMBEDDING_MODEL,
            local_model_path=llm_config.EMBEDDING_MODEL_PATH,
        ),
        "rerank": _model_role_snapshot(
            role="rerank",
            provider_mode=llm_config.RERANK_PROVIDER,
            configured_model=llm_config.RERANK_MODEL,
            local_model_path=llm_config.RERANKER_MODEL_PATH,
        ),
    }
    if llm_config.RERANK_PROVIDER == "local":
        models["rerank"]["device"] = rag_config.RERANKER_DEVICE
    return {"rag_mode": _safe_identifier(rag_config.RAG_MODE), "models": models}


def assert_runtime_environment(expected: object) -> None:
    """配置缺失或漂移时拒绝运行，防止错误比较不同执行环境的结果。"""
    if not isinstance(expected, dict):
        raise RuntimeEnvironmentMismatchError("runtime_snapshot_missing")
    if expected != current_runtime_environment():
        raise RuntimeEnvironmentMismatchError("runtime_environment_changed")
