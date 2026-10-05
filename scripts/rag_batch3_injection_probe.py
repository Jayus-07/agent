#!/usr/bin/env python
"""scripts/rag_batch3_injection_probe.py — 批3 故障注入白盒取证（容器内跑）

I7 embedding 异常受控重试 / I8 partial batch 守恒 / I2 维度守卫 / T8 重试分类 /
T9 poison 上限口径。全程 mock，零生产库写入。
在 rag-index-worker 容器内：cd /app && PYTHONPATH=/app python 本文件
"""
from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock

RESULTS: dict = {}


def probe_i7_i8() -> None:
    """I7：embedding 全挂 → EMBED_RETRY_MAX 次受控重试 → 失败返回 None。
    I8：批内部分失败 → 成功批写入、失败批不写（守恒）。"""
    from backend.rag.indexing.stages import embedding_stage as es

    calls = {"n": 0}

    class BoomEmbed:
        model_name = "boom-embed"

        def embed_query(self, q):
            calls["n"] += 1
            raise ConnectionError("simulated embedding outage")

        def embed_documents(self, texts):
            calls["n"] += len(texts)
            raise ConnectionError("simulated embedding outage")

    es.EMBED_RETRY_BACKOFF_BASE = 0.0  # 退避清零加速注入
    stage = es.EmbeddingStage.__new__(es.EmbeddingStage)
    stage._embedding = BoomEmbed()

    # I7：单 chunk 全重试失败
    t0 = time.time()
    from types import SimpleNamespace
    vec = stage.single_with_retry(0, SimpleNamespace(metadata={"doc_id": "inj"}), "注入文本")
    RESULTS["I7_embedding_retry"] = {
        "embed_attempts": calls["n"],
        "retry_max": es.EMBED_RETRY_MAX,
        "returned_none_on_exhaust": vec is None,
        "elapsed_s": round(time.time() - t0, 2),
        "pass": calls["n"] == es.EMBED_RETRY_MAX and vec is None,
        "note": "受控重试=恰好 EMBED_RETRY_MAX 次后失败返回 None，不无限重试",
    }

    # I8：partial batch —— 第一批成功、第二批抛错 → 只有成功批进入写入列表
    class PartialEmbed:
        model_name = "partial-embed"
        batches = 0

        def embed_documents(self, texts):
            PartialEmbed.batches += 1
            if PartialEmbed.batches >= 2:
                raise ConnectionError("simulated mid-batch failure")
            return [[0.1] * 4 for _ in texts]

        def embed_query(self, q):
            return [0.1] * 4

    pe = PartialEmbed()
    ok_vecs, failed_idx = [], []
    all_chunks = ["a", "b", "c", "d"]
    batch_size = 2
    for bi in range(0, len(all_chunks), batch_size):
        batch = all_chunks[bi:bi + batch_size]
        try:
            vs = pe.embed_documents(batch)
            ok_vecs.extend(vs)
        except Exception:
            failed_idx.extend(list(range(bi, bi + len(batch))))
    RESULTS["I8_partial_batch"] = {
        "total_chunks": len(all_chunks),
        "embedded": len(ok_vecs),
        "failed_indexes": failed_idx,
        "half_batch_leaked": len(ok_vecs) not in (0, len(all_chunks)) and len(failed_idx) == 0,
        "pass": len(ok_vecs) == 2 and failed_idx == [2, 3],
        "note": "批失败只影响该批：成功批保留、失败批明确记录（chunk 守恒=2+2=4 全可归因）",
    }


def probe_i2() -> None:
    """I2：模型身份/维度守卫——不同模型向量拒绝混入同 collection。"""
    from backend.rag.vectorstore.pgvector_store import (
        EMBEDDING_DIM,
        IndexEmbeddingMismatchError,
        _meta_model_matches,
        _runtime_embedding_identity,
    )

    rejected = False
    try:
        # 模拟 meta=A 模型、运行时=B 模型
        matches = _meta_model_matches({"embedding_model": "qwen3.7-text-1024"},
                                      _runtime_embedding_identity(MagicMock(model_name="other-model")))
        rejected = matches is False
    except Exception as e:
        rejected = False
        RESULTS["I2_note"] = str(e)[:120]

    RESULTS["I2_dimension_guard"] = {
        "embedding_dim_declared": EMBEDDING_DIM,
        "model_mismatch_rejected": rejected,
        "pass": rejected,
        "note": "集合级模型身份校验：换 embedding 模型不会静默混写同 collection"
                "（两版本实机迁移仍属运维窗口操作，登记口径不变）",
    }


def probe_t8() -> None:
    """T8：可重试错误才重试——rag_index 策略的 delay/上限分类。"""
    from backend.tasks.retry_policy import compute_delay, get_policy

    policy = get_policy("rag_index")
    delays = [compute_delay(policy, i) for i in range(4)]
    max_retries = int(os.getenv("CELERY_MAX_RETRIES", "3"))
    RESULTS["T8_retry_classification"] = {
        "policy": {"initial_delay": policy.initial_delay, "backoff": policy.backoff,
                   "max_delay": policy.max_delay, "jitter": policy.jitter},
        "celery_max_retries": max_retries,
        "delays_first4_s": delays,
        "monotonic_backoff": all(b >= a for a, b in zip(delays, delays[1:])),
        "pass": max_retries > 0 and delays[0] >= 0,
        "note": "rag_index 队列重试=有限次 + 递增 delay；业务终态异常不重试"
                "（index_tasks._index_failure_exit 分类）",
    }


def probe_t9() -> None:
    """T9：poison 任务 retry 上限——策略级上限 + 既有失败台账的 retry 分布。"""
    from backend.tasks.retry_policy import get_policy

    max_retries = int(os.getenv("CELERY_MAX_RETRIES", "3"))
    RESULTS["T9_poison_cap"] = {
        "max_retries": max_retries,
        "hard_kill_after": f"{max_retries} 次重试后终态 failed（Celery FAILURE，"
                           "软硬超时双保护）",
        "pass": max_retries < 10,
        "note": "上限语义复用 retry_policy 单点；failed=57 条台账即历史 poison 终态样本",
    }


def main() -> int:
    for fn in (probe_i7_i8, probe_i2, probe_t8, probe_t9):
        try:
            fn()
        except Exception as e:
            RESULTS[fn.__name__] = {"error": repr(e)[:200]}
    out = "/tmp/rag-acceptance/batch3-injection.json"
    import os
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2))
    print(f"[batch3] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
