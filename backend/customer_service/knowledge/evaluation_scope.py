"""服务器内部客服评测检索范围；普通 API 输入无法安装此上下文。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

CS_EVAL_KB_ID = "cs_eval_kb"
CS_EVAL_FIXTURE_SET = "baseline"
CS_EVAL_VERSION_ID = "cs-eval-kb-baseline-v1"

_SUPPORTED_SUITES = frozenset({
    "cs_q3_acceptance",
    "cs_q3_action_pool",
    "cs_public_launch_smoke",
})


@dataclass(frozen=True)
class CSKnowledgeEvalScope:
    """一次客服 suite 允许读取的唯一测试 KB。"""

    kb_id: str
    fixture_set: str
    suite: str
    dataset_version: str
    version_id: str


_CURRENT_CS_EVAL_SCOPE: ContextVar[CSKnowledgeEvalScope | None] = ContextVar(
    "current_cs_knowledge_eval_scope",
    default=None,
)


def current_cs_knowledge_eval_scope() -> CSKnowledgeEvalScope | None:
    """返回当前执行上下文内的客服评测范围。"""
    return _CURRENT_CS_EVAL_SCOPE.get()


@contextmanager
def cs_knowledge_eval_scope(
    *,
    kb_id: str,
    fixture_set: str,
    suite: str,
    dataset_version: str,
) -> Iterator[CSKnowledgeEvalScope]:
    """仅接受已登记客服 suite 的测试 KB 范围，并在退出时恢复上下文。"""
    if kb_id != CS_EVAL_KB_ID:
        raise ValueError(f"客服 full_path 只允许 {CS_EVAL_KB_ID}: {kb_id!r}")
    if fixture_set != CS_EVAL_FIXTURE_SET:
        raise ValueError(f"不支持的客服 fixture_set: {fixture_set!r}")
    if suite not in _SUPPORTED_SUITES:
        raise ValueError(f"未登记的客服 full_path suite: {suite!r}")
    if dataset_version != "2.2":
        raise ValueError(f"不支持的客服数据集版本: {dataset_version!r}")

    scope = CSKnowledgeEvalScope(
        kb_id=CS_EVAL_KB_ID,
        fixture_set=CS_EVAL_FIXTURE_SET,
        suite=suite,
        dataset_version=dataset_version,
        version_id=CS_EVAL_VERSION_ID,
    )
    token = _CURRENT_CS_EVAL_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _CURRENT_CS_EVAL_SCOPE.reset(token)
