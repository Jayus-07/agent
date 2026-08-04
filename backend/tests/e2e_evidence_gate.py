"""E2E: Evidence Gate 完整路径验证（不起重型服务）

不走真实 RAGPipeline 初始化（避免 10-15s 预热 + HuggingFace 下载），
而是直接构造 RAGChain 调用核心链路：
  1. _run_evidence_gates（两层 Gate 决策）
  2. build_rejection_response（拒答文本 + RejectInfo）
  3. trace.metadata.rejection 字段写入
  4. trace_store SQLite 持久化可查

三个场景：
  A. NO_EVIDENCE  — 完全空召回
  B. INSUFFICIENT — 召回但 Rerank 多维判定失败
  C. PASS         — 正常高质量命中
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Windows GBK 控制台兜底，避免中文 print 报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from langchain_core.documents import Document

from backend.rag.evidence_gate import (
    RejectReason,
    GateDecision,
    build_rejection_response,
    is_evidence_gate_enabled,
)


def make_rag_chain_mock():
    """构造 RAGChain 但 __init__ 不加载模型/索引

    RAGChain.__init__ 只接受参数 + 调用 _build_retrievers / _build_chains，
    后两者要 doc_db/vectordb/chunk_retriever/bm25，会触发 Chroma/BGE 加载。
    所以这里直接 bypass __init__ 走最小字段。
    """
    from backend.rag.chain import RAGChain
    chain = RAGChain.__new__(RAGChain)
    # RAGChain.__init__ 末尾会赋字段（# Evidence Gate — 诊断态字段），
    # 我们手动设置
    chain.doc_db = None
    chain.vectordb = None
    chain.chunk_retriever = None
    chain.bm25 = None
    chain.person_index = {}
    chain._memory = None
    chain.gate.set_intent("summary_query")
    chain.gate.set_risk_level("low")
    chain.gate.set_query_analysis(None)
    chain._last_query = ""
    # _run_evidence_gates 内部要用
    chain._mq_retriever = MagicMock()
    chain._mq_retriever._last_triggered = False
    return chain


def scenario_no_evidence(chain):
    """场景 A: 完全空召回 → NO_EVIDENCE"""
    from backend.rag import tracer as tracer_mod
    tracer_mod.trace_collector.start("test-no-evidence", session_id="e2e-test")

    context_docs = []  # 全空
    decision = chain._run_evidence_gates("什么是 GDPR 框架？", context_docs)

    assert not decision.passed, f"期望拒答却过了：{decision}"
    assert decision.reason == RejectReason.NO_EVIDENCE, f"reason={decision.reason}"
    print(f"[A] NO_EVIDENCE 通过：decision.reason = {decision.reason.value}")
    print(f"    diagnostics: {decision.diagnostics}")

    # 构造拒答响应
    msg, info = build_rejection_response(decision, "retrieval")
    print(f"[A] 拒答消息: {msg}")
    return info


def scenario_insufficient(chain):
    """场景 B: 召回但 Rerank 多维不达标 → INSUFFICIENT"""
    from backend.rag import tracer as tracer_mod
    tracer_mod.trace_collector.start("test-insufficient", session_id="e2e-test")

    # 模拟：所有 chunk rerank_score 都在阈值附近但不达标
    docs = [
        Document(page_content="sample 1", metadata={
            "chunk_id": "c1", "rerank_score": 0.20,  # < min_top1=0.35
            "doc_type": "general", "source_file": "test.md",
        }),
        Document(page_content="sample 2", metadata={
            "chunk_id": "c2", "rerank_score": 0.05,
            "doc_type": "general", "source_file": "test.md",
        }),
    ]
    chain.gate.set_query_analysis(None)
    decision = chain._run_evidence_gates("低分问题", docs)

    assert not decision.passed, f"期望拒答却过了：{decision}"
    assert decision.reason == RejectReason.INSUFFICIENT, f"reason={decision.reason}"
    print(f"[B] INSUFFICIENT 通过：decision.reason = {decision.reason.value}")
    print(f"    top1={decision.diagnostics.get('top1')} avg={decision.diagnostics.get('avg')}")
    print(f"    failed_rule: {decision.diagnostics.get('failed_rule')}")

    msg, info = build_rejection_response(decision, "rerank")
    print(f"[B] 拒答消息: {msg}")
    return info


def scenario_pass(chain):
    """场景 C: 高质量命中 → 通过"""
    from backend.rag import tracer as tracer_mod
    tracer_mod.trace_collector.start("test-pass", session_id="e2e-test")

    docs = [
        Document(page_content="高质量答案 1", metadata={
            "chunk_id": "c1", "rerank_score": 0.85, "rrf_score": 0.7,
            "doc_type": "policy", "source_file": "compliance.md",
        }),
        Document(page_content="高质量答案 2", metadata={
            "chunk_id": "c2", "rerank_score": 0.72,
            "doc_type": "policy", "source_file": "compliance.md",
        }),
        Document(page_content="高质量答案 3", metadata={
            "chunk_id": "c3", "rerank_score": 0.60,
            "doc_type": "policy", "source_file": "compliance.md",
        }),
    ]
    chain.gate.set_query_analysis(None)
    decision = chain._run_evidence_gates("GDPR 合规要求", docs)

    assert decision.passed, f"期望通过却拒了：{decision}"
    print(f"[C] PASS 通过：top1={decision.diagnostics.get('top1')} "
          f"avg={decision.diagnostics.get('avg')} gap={decision.diagnostics.get('gap')}")
    return decision


def scenario_trace_persistence(reject_info):
    """场景 D: trace_store SQLite 持久化 RejectInfo

    关键：用临时 db 隔离生产 data/trace_store.db，避免污染历史数据。
    跑完清理临时 db（不影响 get_trace_store 单例缓存）。
    """
    import tempfile
    import time
    from backend.rag import trace_store as ts_mod

    tmp_db = Path(tempfile.gettempdir()) / f"e2e_trace_{int(time.time()*1000)}.db"
    tmp_store = ts_mod.TraceStore(db_path=str(tmp_db))

    # 临时替换 _trace_store 单例，避免 get_trace_store() 锁回生产 db
    original_singleton = ts_mod._trace_store
    ts_mod._trace_store = tmp_store
    try:
        trace_id = f"e2e-test-{int(time.time()*1000)}"
        fake_trace = {
            "id": trace_id,
            "request_id": trace_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": "e2e-test",
            "question": "e2e trace 持久化测试",
            "answer_preview": "[REJECTED]",
            "answer_len": 0,
            "duration_ms": 50,
            "model": "",
            "provider": "",
            "spans": [],
            "metadata": {
                "rejection": reject_info.to_dict(),
            },
        }
        tmp_store.save(fake_trace)

        # 验证：通过 list_since(only_rejected=True) 能查到我们刚写入的
        only_rejected = tmp_store.list_since(
            "1970-01-01 00:00:00", only_rejected=True, limit=50
        )
        matched = [r for r in only_rejected if r["id"] == trace_id]
        assert len(matched) == 1, \
            f"trace {trace_id} 在 only_rejected 查询中应唯一存在，实际 {len(matched)} 条"
        assert matched[0]["metadata"]["rejection"]["reason"] == reject_info.reason
        print(f"[D] SQLite 持久化通过：trace_id={trace_id}")
        print(f"    rejection.reason = {reject_info.reason}")
        print(f"    only_rejected 查得 1 条；非 reject 应被过滤")

        # 交叉验证：再写一条非 reject，list_since(only_rejected=True) 不应包含它
        non_rejected_id = f"e2e-non-reject-{int(time.time()*1000)}"
        tmp_store.save({
            "id": non_rejected_id,
            "request_id": non_rejected_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": "e2e-test",
            "question": "非拒答样例",
            "answer_preview": "ok",
            "answer_len": 2,
            "duration_ms": 10,
            "model": "",
            "provider": "",
            "spans": [],
            "metadata": {},  # 没有 rejection
        })
        only_rejected_2 = tmp_store.list_since(
            "1970-01-01 00:00:00", only_rejected=True, limit=50
        )
        matched_non = [r for r in only_rejected_2 if r["id"] == non_rejected_id]
        assert len(matched_non) == 0, \
            f"非拒答 trace 应被 only_rejected 过滤掉，实际命中 {len(matched_non)}"
        print(f"    过滤非拒答 OK（non_rejected_id 未在 only_rejected 中）")

        all_results = tmp_store.list_since(
            "1970-01-01 00:00:00", only_rejected=False, limit=50
        )
        print(f"    list_since(默认) 共 {len(all_results)} 条（含我们写的 2 条）")
        print(f"    隔离 db: {tmp_db}（跑完自动清理）")
    finally:
        ts_mod._trace_store = original_singleton
        try:
            tmp_db.unlink()
        except Exception:
            pass


def main():
    print("=" * 60)
    print("E2E: Evidence Gate 完整路径验证")
    print("=" * 60)

    print(f"\n[系统] is_evidence_gate_enabled = {is_evidence_gate_enabled()}")
    assert is_evidence_gate_enabled(), "总开关应默认开"

    chain = make_rag_chain_mock()
    print(f"[系统] RAGChain mock 构造完成（绕过重型初始化）\n")

    # ── 场景 A ──
    print("─" * 60)
    print("[场景 A] 完全空召回 → NO_EVIDENCE")
    print("─" * 60)
    info_a = scenario_no_evidence(chain)

    # ── 场景 B ──
    print("\n" + "─" * 60)
    print("[场景 B] 召回但 Rerank 不达标 → INSUFFICIENT")
    print("─" * 60)
    info_b = scenario_insufficient(chain)

    # ── 场景 C ──
    print("\n" + "─" * 60)
    print("[场景 C] 高质量命中 → PASS")
    print("─" * 60)
    scenario_pass(chain)

    # ── 场景 D ──
    print("\n" + "─" * 60)
    print("[场景 D] trace_store SQLite 持久化")
    print("─" * 60)
    scenario_trace_persistence(info_a)

    # ── 总览 ──
    print("\n" + "=" * 60)
    print("E2E 全部通过")
    print("=" * 60)
    print(f"  A. NO_EVIDENCE   reason={info_a.reason}  layer={info_a.layer}")
    print(f"  B. INSUFFICIENT  reason={info_b.reason}  layer={info_b.layer}")
    print("  C. PASS          decision.passed=True")
    print("  D. PERSISTED     trace_store.list_since(only_rejected=True) 命中")

    return 0


if __name__ == "__main__":
    sys.exit(main())
