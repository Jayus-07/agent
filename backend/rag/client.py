"""rag/client.py — RAGPipeline 远端代理（RAG_MODE=remote 时生效）

设计原则：换引擎不换接口。代理与 RAGPipeline 保持 ask / retrieve_knowledge /
last_answer_meta 同形，因此 tools/rag.py、skills/business_analysis、
customer_service/knowledge、mcp_servers/servers/rag.py 等消费方零改动。

边界划分：
  - 问答面（ask / retrieve_knowledge / last_answer_meta）：代理覆盖
  - 索引管理面（vectordb / 上传 / 一致性检查）：不代理，remote 模式下访问
    即抛错并给出指引 —— 这些能力归属 rag-service（或切 RAG_MODE=local）
"""
import threading
from collections.abc import Iterable
from typing import Any

import httpx

from backend.shared.logger import logger

# ask 完整链路（检索→rerank→LLM→evidence gate）30-120s，超时需覆盖最坏情况
_ASK_TIMEOUT_S = 300.0
# retrieve 只走检索不生成，~3-5s
_RETRIEVE_TIMEOUT_S = 30.0
# 审核拒绝会执行 BM25 重建，允许覆盖中等规模知识库的清理耗时
_REVIEW_TIMEOUT_S = 300.0


class RAGServiceProxy:
    """RAG 服务远端代理 —— 与 RAGPipeline 公共入口同签名。

    httpx.Client 线程安全可复用；每次 ask 后由服务端返回 meta，
    填入 last_answer_meta 供 customer_service 等下游消费。
    """

    def __init__(self, base_url: str | None = None, transport: httpx.BaseTransport | None = None):
        from backend.config.rag import RAG_SERVICE_URL
        self._base_url = (base_url or RAG_SERVICE_URL).rstrip("/")
        # 长短超时分离：connect 短（服务不在快速失败），read 长（等待生成）
        self._client = httpx.Client(
            base_url=self._base_url,
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=_ASK_TIMEOUT_S, write=10.0, pool=5.0),
        )
        self.last_answer_meta: dict = {}
        # 最近一次检索的索引状态（"" = rag-service 未上报；"stale" = 索引未就绪，
        # 旧快照服务中）——/rag/search 等消费方透出给前端
        self.last_index_status: str = ""
        logger.info(f"[RAGProxy] 远端模式 endpoint={self._base_url}")

    # ── 问答面（与 RAGPipeline 同签名）──

    def ask(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        kb_ids: list[str] | None = None,
        subject_type: str = "",
        department: str = "",
        permissions: Iterable[str] | None = None,
        user_id: str = "",
        tenant_id: str = "",
        roles: tuple[str, ...] = (),
    ) -> str:
        try:
            resp = self._client.post(
                "/ask",
                json={
                    "question": question,
                    "session_id": session_id,
                    "kb_id": kb_id,
                    "kb_ids": kb_ids,
                    "subject_type": subject_type,
                    "department": department,
                    "permissions": permissions,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "roles": list(roles),
                },
                timeout=_ASK_TIMEOUT_S,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"RAG 服务调用失败(ask): {e} —— 检查 rag-service({self._base_url}) "
                f"是否运行及 /readyz 状态"
            ) from e
        payload = resp.json()
        self.last_answer_meta = payload.get("meta") or {}
        return payload["answer"]

    def retrieve_knowledge(
        self,
        question: str,
        kb_id: str = "default",
        top_k: int = 3,
        subject_type: str = "",
        department: str = "",
        permissions: Iterable[str] | None = None,
        roles: tuple[str, ...] = (),
    ) -> str:
        try:
            resp = self._client.post(
                "/retrieve",
                json={
                    "question": question,
                    "kb_id": kb_id,
                    "top_k": top_k,
                    "subject_type": subject_type,
                    "department": department,
                    "permissions": permissions,
                    "roles": list(roles),
                },
                timeout=_RETRIEVE_TIMEOUT_S,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"RAG 服务调用失败(retrieve): {e} —— 检查 rag-service({self._base_url}) "
                f"是否运行及 /readyz 状态"
            ) from e
        payload = resp.json()
        self.last_index_status = payload.get("index_status", "")
        return payload["result"]

    def retrieve_documents(
        self,
        question: str,
        kb_id: str = "default",
        top_k: int = 5,
        subject_type: str = "",
        department: str = "",
        permissions: Iterable[str] | None = None,
        roles: tuple[str, ...] = (),
    ) -> list:
        """授权检索的文档级出口（/rag/search remote 分支）。

        rag-service /retrieve_docs 执行与本地 retrieve_documents 同一授权
        语义（KB keep-set + permission_scope 后过滤），序列化回 Document
        形态（metadata.score = 向量距离，BM25 补充结果无 score）。
        """
        try:
            resp = self._client.post(
                "/retrieve_docs",
                json={
                    "question": question,
                    "kb_id": kb_id,
                    "top_k": top_k,
                    "subject_type": subject_type,
                    "department": department,
                    "permissions": permissions,
                    "roles": list(roles),
                },
                timeout=_RETRIEVE_TIMEOUT_S,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"RAG 服务调用失败(retrieve_docs): {e} —— 检查 rag-service"
                f"({self._base_url}) 是否运行及版本是否含 /retrieve_docs"
            ) from e
        payload = resp.json()
        self.last_index_status = payload.get("index_status", "")
        from langchain_core.documents import Document

        return [
            Document(page_content=item.get("content") or "",
                     metadata=dict(item.get("metadata") or {}))
            for item in (payload.get("docs") or [])
        ]

    def ask_result(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        kb_ids: list[str] | None = None,
        subject_type: str = "",
        department: str = "",
        permissions: Iterable[str] | None = None,
        user_id: str = "",
        tenant_id: str = "",
        roles: tuple[str, ...] = (),
    ):
        """问答面补齐（2026-09-28 生产冒烟实测缺陷）：/rag/ask 路由调用
        ask_result，此前代理未覆盖 → remote 部署形态下该 API 恒 500。
        复用 /ask（meta 随响应带回），映射为 AskOutcome 同形返回。
        """
        from backend.rag.pipeline import AskOutcome

        answer = self.ask(
            question, session_id=session_id, kb_id=kb_id, kb_ids=kb_ids,
            subject_type=subject_type, department=department,
            permissions=permissions, user_id=user_id, tenant_id=tenant_id,
            roles=roles,
        )
        meta = dict(self.last_answer_meta or {})
        sources = list(meta.pop("sources", []) or [])
        return AskOutcome(answer=answer, sources=sources, answer_meta=meta)

    def review_pending_doc(self, doc_id: str, action: str) -> dict[str, Any]:
        """把待复核审核动作转发给持有本地索引的 rag-service。"""
        if action not in {"approve", "reject"}:
            raise ValueError(f"无效审核动作: {action}")
        from backend.config.messaging import AI_INTERNAL_TOKEN

        headers = {}
        if AI_INTERNAL_TOKEN:
            headers["X-Internal-Token"] = AI_INTERNAL_TOKEN
        try:
            resp = self._client.post(
                f"/admin/pending/{doc_id}/decision",
                json={"action": action},
                headers=headers,
                timeout=_REVIEW_TIMEOUT_S,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"RAG 审核动作失败({action}): {exc} —— 检查 rag-service 是否就绪"
            ) from exc
        return resp.json()

    @property
    def is_index_stale(self) -> bool:
        """最近一次检索的索引状态（C 阶段热刷新语义，与本地 pipeline 同形）。

        rag-service 每次 /retrieve、/retrieve_docs 响应都带 index_status；
        显式属性而非 __getattr__ 兜底——代理的管理面拒绝语义必须保持。
        """
        return self.last_index_status == "stale"

    # ── 索引管理面：明确拒绝 ──

    def __getattr__(self, name: str) -> Any:
        # 注意：这里不能引用任何实例属性（含 _base_url），
        # 否则属性未初始化时 __getattr__ 自我递归爆栈
        raise RuntimeError(
            f"RAG_MODE=remote 下不可访问 RAGPipeline.{name}（索引管理面未代理）。"
            f"文档上传/删除/一致性检查等请直连 rag-service "
            f"(RAG_SERVICE_URL，见 backend/config/rag.py) 或临时切回 RAG_MODE=local。"
        )


# ── 代理单例（与 pipeline._pipeline_singleton 同模式）──
_proxy_lock = threading.Lock()
_proxy_singleton: RAGServiceProxy | None = None


def get_rag_proxy() -> RAGServiceProxy:
    """惰性构造远端代理单例（构造只建 HTTP client，无阻塞）。"""
    global _proxy_singleton
    if _proxy_singleton is None:
        with _proxy_lock:
            if _proxy_singleton is None:
                _proxy_singleton = RAGServiceProxy()
    return _proxy_singleton
